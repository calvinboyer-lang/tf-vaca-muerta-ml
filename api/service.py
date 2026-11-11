"""Lógica de la API, independiente de FastAPI (se testea sin servidor).

- Carga la versión vigente de cada modelo desde el store (local o gs://),
  verificando el sha256 registrado en current.json.
- Recarga: un hilo revisa current.json cada RELOAD_INTERVAL_S segundos y, si
  cambió la versión, carga la nueva y la reemplaza de forma atómica. Así todas
  las réplicas convergen sin reiniciar pods. POST /admin/reload fuerza la
  revisión en la réplica que atiende la petición.
- Si una carga falla, se conserva la versión anterior (si la había) y el error
  queda en /models y en la métrica model_reload_total{result="error"}.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass

import numpy as np
import pandas as pd

from vm_ml import registry
from vm_ml.data import load_snapshot, month_label
from vm_ml.features import feature_columns, prepare_features
from vm_ml.storage import open_store
from vm_ml.wells import WellIndex, build_request_row


class ModelNotLoaded(RuntimeError):
    pass


class WellNotFound(LookupError):
    pass


@dataclass
class ModelHandle:
    name: str
    version: str
    sha256: str
    pipeline: object
    metadata: dict
    loaded_utc: str

    def info(self) -> dict:
        m = self.metadata
        return {
            "model": self.name,
            "version": self.version,
            "use": m.get("use"),
            "dataset": m["dataset"]["dataset_id"],
            "features": m["features"]["numeric"] + m["features"]["categorical"],
            "params": m.get("params"),
            "holdout": m["metrics"]["holdout"],
            "cv_mae": m["validation"]["repeated"]["tree"]["summary"]["mae"],
            "loaded_utc": self.loaded_utc,
        }


class ServingState:
    def __init__(self, store_uri: str, models: list[str], default_model: str, on_reload=None):
        self.store = open_store(store_uri)
        self.store_uri = store_uri
        self.model_names = models
        self.default_model = default_model
        self.handles: dict[str, ModelHandle] = {}
        self.errors: dict[str, str] = {}
        self.wells: WellIndex | None = None
        self._lock = threading.Lock()
        self._on_reload = on_reload or (lambda name, result, version: None)
        self._stop = threading.Event()

    # -- carga ---------------------------------------------------------------
    def ready(self) -> bool:
        return self.default_model in self.handles

    def reload(self) -> dict:
        """Revisa current.json de cada modelo y carga lo que haya cambiado."""
        report = {}
        for name in self.model_names:
            try:
                pointer = registry.current_pointer(self.store, name)
                if pointer is None:
                    report[name] = "sin versión vigente"
                    self.errors[name] = "sin versión vigente en el store"
                    continue
                current = self.handles.get(name)
                if current and current.version == pointer["version"]:
                    report[name] = f"sin cambios ({current.version})"
                    continue
                pipeline = registry.load_pipeline(self.store, name, pointer["version"], pointer["sha256"])
                metadata = registry.read_metadata(self.store, name, pointer["version"])
                handle = ModelHandle(name, pointer["version"], pointer["sha256"], pipeline, metadata, registry.utc_now())
                with self._lock:
                    self.handles[name] = handle
                self.errors.pop(name, None)
                report[name] = f"cargada {handle.version}"
                self._on_reload(name, "ok", handle.version)
            except Exception as exc:  # se conserva la versión anterior
                self.errors[name] = str(exc)
                report[name] = f"error: {exc}"
                self._on_reload(name, "error", None)
        self._refresh_wells()
        return report

    def _refresh_wells(self) -> None:
        handle = self.handles.get(self.default_model) or next(iter(self.handles.values()), None)
        if handle is None:
            return
        ds = handle.metadata["dataset"]
        if self.wells is not None and self.wells.dataset_id == ds["dataset_id"]:
            return
        try:
            key = ds.get("store_key") or f"datasets/{ds['dataset_id']}.csv.gz"
            data = self.store.read_bytes(key)
            df = pd.read_csv(io.BytesIO(data), compression="gzip", low_memory=False)
            self.wells = WellIndex(df, ds["dataset_id"])
        except Exception as exc:
            self.errors["wells"] = f"no se pudo cargar el índice de pozos: {exc}"

    def start_background_reload(self, interval_s: float) -> None:
        if interval_s <= 0:
            return

        def loop():
            while not self._stop.wait(interval_s):
                self.reload()

        threading.Thread(target=loop, name="model-reload", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    # -- predicción -----------------------------------------------------------
    def handle(self, name: str | None) -> ModelHandle:
        name = name or self.default_model
        if name not in self.model_names:
            raise ValueError(f"modelo desconocido: {name}. Opciones: {self.model_names}")
        handle = self.handles.get(name)
        if handle is None:
            raise ModelNotLoaded(f"el modelo {name} no está cargado: {self.errors.get(name, 'cargando')}")
        return handle

    def predict(self, model: str | None, idpozo: int | None, anio: int | None, mes: int | None, overrides: dict) -> dict:
        handle = self.handle(model)
        well = None
        if idpozo is not None:
            if self.wells is None:
                raise ModelNotLoaded("el índice de pozos no está disponible; envíe las variables explícitas")
            well = self.wells.get(idpozo)
            if well is None:
                raise WellNotFound(f"pozo {idpozo} no está en el dataset {self.wells.dataset_id}")
        if (anio is None) != (mes is None):
            raise ValueError("anio y mes van juntos")
        period = anio * 12 + mes - 1 if anio is not None else None
        if well is None and period is None and "mes" in feature_columns(handle.name)[1]:
            raise ValueError("sin idpozo hay que indicar anio y mes")

        row, sources = build_request_row(well, period, overrides)
        numeric, categorical = feature_columns(handle.name)
        missing = [c for c in numeric + categorical if row.get(c) is None]
        if missing:
            raise ValueError(f"faltan variables para {handle.name}: {missing} (envíelas o indique un idpozo)")
        X = prepare_features(pd.DataFrame([row]), handle.name)
        raw = float(handle.pipeline.predict(X)[0])
        stats = handle.metadata.get("target_stats_train", {})
        upper = 1.5 * stats["max"] if "max" in stats else np.inf
        out_of_range = raw < 0 or raw > upper
        unknown = [
            c for c in categorical
            if str(X[c].iloc[0]) not in handle.metadata["features"].get("categories", {}).get(c, [str(X[c].iloc[0])])
        ]
        return {
            "model": handle.name,
            "version": handle.version,
            "use": handle.metadata.get("use"),
            "idpozo": idpozo,
            "periodo": row.get("periodo"),
            "prediccion_m3_mes": round(max(raw, 0.0), 1),
            "fuera_de_rango": out_of_range,
            "categorias_no_vistas": unknown,
            "entrada": {c: row.get(c) for c in numeric + categorical},
            "fuentes": sources,
            "referencia_mae_holdout": round(handle.metadata["metrics"]["holdout"]["mae"], 1),
        }

    def well_history(self, idpozo: int) -> dict:
        """Producción real del pozo y lo que estima cada modelo vigente, mes a mes."""
        if self.wells is None:
            raise ModelNotLoaded("el índice de pozos no está disponible")
        well = self.wells.get(idpozo)
        if well is None:
            raise WellNotFound(f"pozo {idpozo} no está en el dataset {self.wells.dataset_id}")
        hist = well.history.copy()
        hist["periodo"] = hist["mes_idx"].map(month_label)
        rows = []
        for _, h in hist.iterrows():
            base = {
                "empresa": well.empresa,
                "areayacimiento": well.areayacimiento,
                "profundidad": well.profundidad,
                "tipoextraccion": h.get("tipoextraccion") or well.tipoextraccion,
                "tef": h.get("tef"),
                "mes": int(h["mes"]),
                "edad_meses": (int(h["mes_idx"]) - well.primera_prod_idx) if well.primera_prod_idx is not None else None,
            }
            rows.append(base)
        frame = pd.DataFrame(rows)
        estimates = {}
        for name, handle in self.handles.items():
            try:
                estimates[name] = [round(float(v), 1) for v in handle.pipeline.predict(prepare_features(frame, name))]
            except Exception as exc:
                estimates[name] = f"error: {exc}"
        return {
            "pozo": well.summary(),
            "meses": [
                {
                    "periodo": p,
                    "prod_pet_real": None if pd.isna(r) else float(r),
                    "tef": None if pd.isna(t) else float(t),
                    "tipoestado": e,
                    **{f"estimado_{n}": (v[i] if isinstance(v, list) else None) for n, v in estimates.items()},
                }
                for i, (p, r, t, e) in enumerate(zip(hist["periodo"], hist["prod_pet"], hist["tef"], hist.get("tipoestado", [None] * len(hist))))
            ],
            "versiones": {n: h.version for n, h in self.handles.items()},
        }

    def models_info(self) -> dict:
        return {
            "default_model": self.default_model,
            "store": self.store_uri,
            "models": {n: h.info() for n, h in self.handles.items()},
            "errors": self.errors,
            "wells_dataset": self.wells.dataset_id if self.wells else None,
            "wells_count": len(self.wells) if self.wells else 0,
        }

