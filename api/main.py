"""API de inferencia: producción mensual de petróleo de pozos shale de Vaca Muerta.

Variables de entorno:
  STORE_URI            carpeta local (/artifacts) o gs://<bucket>/<prefijo>   [./artifacts]
  MODELS               modelos a servir, separados por coma                  [v1,v2,v2_notef]
  DEFAULT_MODEL        modelo de /predict si no se indica                     [v2_notef]
  RELOAD_INTERVAL_S    cada cuánto revisar current.json (0 = nunca)           [300]
  ADMIN_TOKEN          si existe, POST /admin/reload exige X-Admin-Token
  DATABASE_URL         PostgreSQL para registrar predicciones (opcional)
  PORT                 [8081]

Endpoints principales: POST /predict, GET /healthz, GET /livez, GET /metrics,
GET /models, GET /models/{model}/versions, GET /wells/{idpozo},
GET /wells?q=..., GET /stats, POST /admin/reload. Documentación interactiva en /docs.
"""

from __future__ import annotations

import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response  # noqa: E402
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from api.dblog import PredictionLogger, recent_stats  # noqa: E402
from api.service import ModelNotLoaded, ServingState, WellNotFound  # noqa: E402
from vm_ml import registry  # noqa: E402

STORE_URI = os.environ.get("STORE_URI", "./artifacts")
MODELS = [m.strip() for m in os.environ.get("MODELS", "v1,v2,v2_notef").split(",") if m.strip()]
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "v2_notef")
RELOAD_INTERVAL_S = float(os.environ.get("RELOAD_INTERVAL_S", "300"))
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
POD = os.environ.get("HOSTNAME", socket.gethostname())

# --- métricas Prometheus ------------------------------------------------------
REQUESTS = Counter("predict_requests_total", "Peticiones a /predict", ["model", "status"])
LATENCY = Histogram(
    "predict_latency_seconds",
    "Latencia de /predict (incluye búsqueda del pozo y preprocesamiento)",
    ["model"],
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
PREDICTION = Histogram(
    "predict_value_m3",
    "Valor estimado de producción mensual (m3)",
    ["model"],
    buckets=(0, 100, 250, 500, 1000, 2000, 4000, 8000, 16000, 32000),
)
OUT_OF_RANGE = Counter("predict_out_of_range_total", "Predicciones fuera del rango físico/entrenado", ["model"])
MODEL_INFO = Gauge("model_info", "Versión vigente cargada en esta réplica (valor 1)", ["model", "version"])
MODEL_HOLDOUT_MAE = Gauge("model_holdout_mae_m3", "MAE en el holdout fijo de la versión cargada", ["model"])
RELOADS = Counter("model_reload_total", "Cargas de modelo", ["model", "result"])
DB_ERRORS = Counter("db_write_errors_total", "Predicciones que no se pudieron guardar en PostgreSQL")


def _on_reload(name: str, result: str, version: str | None) -> None:
    RELOADS.labels(name, result).inc()
    if result == "ok" and version:
        for sample in list(MODEL_INFO.collect()[0].samples):
            if sample.labels.get("model") == name:
                MODEL_INFO.remove(name, sample.labels["version"])
        MODEL_INFO.labels(name, version).set(1)
        handle = state.handles.get(name)
        if handle:
            MODEL_HOLDOUT_MAE.labels(name).set(handle.metadata["metrics"]["holdout"]["mae"])


state = ServingState(STORE_URI, MODELS, DEFAULT_MODEL, on_reload=_on_reload)
pred_log = PredictionLogger(on_error=lambda n: DB_ERRORS.inc(n))


@asynccontextmanager
async def lifespan(_: FastAPI):
    print(f"[api] store={STORE_URI} modelos={MODELS} default={DEFAULT_MODEL}", flush=True)
    print(f"[api] carga inicial: {state.reload()}", flush=True)
    state.start_background_reload(RELOAD_INTERVAL_S)
    yield
    state.stop()
    pred_log.stop()


app = FastAPI(
    title="TF Vaca Muerta: producción mensual de petróleo",
    version="1.0.0",
    description="Estimación académica de producción mensual (m³) de pozos shale de Vaca Muerta. "
    "No es un pronóstico industrial validado.",
    lifespan=lifespan,
)


class PredictRequest(BaseModel):
    model: str | None = Field(None, description="v1 | v2 | v2_notef (default: DEFAULT_MODEL)")
    idpozo: int | None = Field(None, description="Si se envía, la API completa las variables del pozo")
    anio: int | None = Field(None, ge=2000, le=2100, description="Mes a estimar (default: el siguiente al último dato)")
    mes: int | None = Field(None, ge=1, le=12)
    tef: float | None = Field(None, ge=0, le=31, description="Días efectivos de producción (v1 y v2)")
    profundidad: float | None = Field(None, gt=0, le=12000)
    edad_meses: int | None = Field(None, ge=0, le=600)
    tipoextraccion: str | None = None
    empresa: str | None = None
    areayacimiento: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"idpozo": 165715},
                {"idpozo": 165715, "anio": 2026, "mes": 7, "model": "v2"},
                {"model": "v2_notef", "anio": 2026, "mes": 3, "profundidad": 3000, "edad_meses": 6,
                 "tipoextraccion": "Surgencia Natural", "empresa": "YPF S.A.", "areayacimiento": "LOMA CAMPANA-LLL"},
            ]
        }
    }


@app.get("/")
def index() -> dict:
    return {
        "service": "tf-vaca-muerta-api",
        "pod": POD,
        "endpoints": ["POST /predict", "GET /healthz", "GET /livez", "GET /metrics", "GET /models",
                      "GET /models/{model}/versions", "GET /wells/{idpozo}", "GET /wells?q=", "GET /wells/sample", "GET /stats",
                      "POST /admin/reload", "GET /docs"],
        "example": {"idpozo": 165715},
    }


@app.get("/healthz")
def healthz() -> Response:
    """Readiness: 200 solo si el modelo por defecto está cargado (si no, Kubernetes no le manda tráfico)."""
    if not state.ready():
        return Response('{"status":"loading"}', status_code=503, media_type="application/json")
    return Response('{"status":"ok"}', media_type="application/json")


@app.get("/livez")
def livez() -> dict:
    """Liveness: el proceso responde (no depende del modelo ni de la base)."""
    return {"status": "alive"}


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/predict")
def predict(req: PredictRequest) -> dict:
    model = req.model or DEFAULT_MODEL
    start = time.perf_counter()
    status, result = 200, None
    overrides = req.model_dump(include={"tef", "profundidad", "edad_meses", "tipoextraccion", "empresa", "areayacimiento"})
    try:
        result = state.predict(model, req.idpozo, req.anio, req.mes, overrides)
        return result
    except ValueError as exc:
        status = 422
        raise HTTPException(422, str(exc)) from exc
    except WellNotFound as exc:
        status = 404
        raise HTTPException(404, str(exc)) from exc
    except ModelNotLoaded as exc:
        status = 503
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # error inesperado: 500 y queda en el log
        status = 500
        print(f"[predict] error: {exc!r}", flush=True)
        raise HTTPException(500, "error interno al predecir") from exc
    finally:
        elapsed = time.perf_counter() - start
        model_label = model if model in MODELS else "desconocido"
        REQUESTS.labels(model_label, str(status)).inc()
        LATENCY.labels(model_label).observe(elapsed)
        if result is not None:
            PREDICTION.labels(model_label).observe(result["prediccion_m3_mes"])
            if result["fuera_de_rango"]:
                OUT_OF_RANGE.labels(model_label).inc()
        pred_log.log({
            "ts_utc": datetime.now(timezone.utc),
            "model": model_label,
            "version": result["version"] if result else "",
            "idpozo": req.idpozo,
            "request": req.model_dump(exclude_none=True),
            "prediction": result["prediccion_m3_mes"] if result else None,
            "out_of_range": bool(result and result["fuera_de_rango"]),
            "latency_ms": round(elapsed * 1000, 3),
            "status_code": status,
            "pod": POD,
        })


@app.get("/models")
def models() -> dict:
    return state.models_info()


@app.get("/models/{model}/versions")
def model_versions(model: str) -> dict:
    if model not in MODELS:
        raise HTTPException(404, f"modelo desconocido: {model}")
    current = registry.current_pointer(state.store, model) or {}
    out = []
    for m in registry.list_versions(state.store, model):
        out.append({
            "version": m["version"],
            "vigente": m["version"] == current.get("version"),
            "dataset": m["dataset"]["dataset_id"],
            "params": m["params"],
            "holdout": m["metrics"]["holdout"],
            "cv_mae": m["validation"]["repeated"]["tree"]["summary"]["mae"],
            "fit_s": m["durations_s"]["fit"],
            "promovida": m["promotion"]["promoted"],
            "decision": m["promotion"]["reason"],
        })
    return {"model": model, "current": current, "versions": out}


@app.get("/wells")
def wells_search(q: str = Query(..., min_length=2), limit: int = Query(20, le=100)) -> dict:
    if state.wells is None:
        raise HTTPException(503, "índice de pozos no disponible")
    return {"dataset": state.wells.dataset_id, "resultados": state.wells.search(q, limit)}


@app.get("/wells/sample")
def wells_sample(n: int = Query(200, ge=1, le=5000)) -> dict:
    """Ids de pozos del dataset (para pruebas de carga y ejemplos)."""
    if state.wells is None:
        raise HTTPException(503, "índice de pozos no disponible")
    return {"dataset": state.wells.dataset_id, "idpozo": state.wells.sample_ids(n)}


@app.get("/wells/{idpozo}")
def well_detail(idpozo: int) -> dict:
    try:
        return state.well_history(idpozo)
    except WellNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ModelNotLoaded as exc:
        raise HTTPException(503, str(exc)) from exc


@app.get("/stats")
def stats(minutes: int = Query(60, ge=1, le=10080)) -> dict:
    try:
        data = recent_stats(minutes)
    except Exception as exc:
        raise HTTPException(503, f"PostgreSQL no disponible: {exc}") from exc
    if data is None:
        raise HTTPException(503, "DATABASE_URL no configurada")
    return data


@app.post("/admin/reload")
def admin_reload(request: Request, x_admin_token: str | None = Header(None)) -> dict:
    """Fuerza la revisión de current.json en ESTA réplica (las demás lo hacen solas cada RELOAD_INTERVAL_S)."""
    if ADMIN_TOKEN and x_admin_token != ADMIN_TOKEN:
        raise HTTPException(401, "token inválido")
    return {"pod": POD, "resultado": state.reload()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8081")), access_log=False)
