# Especificación de la API

Base: `http://<host>:8081`. Documentación interactiva (OpenAPI) en `/docs`.
Los ejemplos se muestran con `Invoke-RestMethod` (PowerShell) y con `curl`.

> Los ejemplos 1, `/wells/{idpozo}` y `/models` son **respuestas reales** de la API desplegada en GKE el 12/11/2026 (capturas 5.5 y 2.5b; ver `docs/resultados.md` §3.0.1). Los ejemplos 2 a 4 muestran el formato del pedido y del error.

## Resumen

| Método | Ruta | Uso |
|---|---|---|
| POST | `/predict` | Estimación de producción mensual (m³) |
| GET | `/healthz` | Readiness: 200 si el modelo por defecto está cargado, 503 si no |
| GET | `/livez` | Liveness: el proceso responde |
| GET | `/metrics` | Métricas Prometheus |
| GET | `/models` | Versión vigente de cada modelo, métricas y errores de carga |
| GET | `/models/{model}/versions` | Historial de versiones y decisiones de promoción |
| GET | `/wells/{idpozo}` | Datos del pozo, producción real y estimación de cada modelo mes a mes |
| GET | `/wells?q=texto` | Búsqueda de pozos por id, operadora o área |
| GET | `/wells/sample?n=200` | Ids de pozos de la población (pruebas de carga) |
| GET | `/stats?minutes=60` | Resumen desde PostgreSQL: predicciones (latencias, errores) y entrenamientos |
| POST | `/admin/reload` | Fuerza la recarga de `current.json` en la réplica (`X-Admin-Token` si `ADMIN_TOKEN` está definido) |

## POST /predict

### Entrada (JSON)

| Campo | Tipo | Obligatorio | Descripción |
|---|---|---|---|
| `model` | string | no | `v1`, `v2` o `v2_notef` (por defecto `v2_notef`) |
| `idpozo` | int | no* | Si se envía, la API completa edad, operadora, área, profundidad y sistema de extracción desde el dataset |
| `anio`, `mes` | int | no* | Mes a estimar (van juntos). Por defecto, el mes siguiente al último dato del pozo |
| `tef` | float 0–31 | no | Días efectivos de producción (v1 y v2). Si falta, se supone el mes completo |
| `profundidad` | float | no | m |
| `edad_meses` | int | no | Meses desde el inicio del pozo |
| `tipoextraccion`, `empresa`, `areayacimiento` | string | no | Categorías tal como figuran en los datos de la Secretaría |

\* Se necesita `idpozo`, o bien `anio`/`mes` junto con todas las variables del modelo elegido. Lo que se envía explícitamente **sobrescribe** lo que sale del pozo.

### Salida (200)

| Campo | Descripción |
|---|---|
| `model`, `version`, `use` | Modelo y versión que respondieron, y su uso (descriptivo o pronóstico) |
| `idpozo`, `periodo` | Pozo y mes estimado (`AAAA-MM`) |
| `prediccion_m3_mes` | Estimación (m³/mes), recortada a ≥ 0 |
| `fuera_de_rango` | `true` si el valor crudo es < 0 o > 1,5 × el máximo de entrenamiento |
| `categorias_no_vistas` | Variables categóricas con valores que el modelo no vio al entrenar |
| `entrada` | Variables efectivamente usadas |
| `fuentes` | De dónde salió cada dato (pozo, valores enviados, supuestos) |
| `referencia_mae_holdout` | MAE del modelo en el holdout fijo, como orden de magnitud del error |

### Errores

| Código | Caso |
|---|---|
| 404 | `idpozo` no está en el dataset |
| 422 | Validación: faltan variables, mes fuera de rango, `anio` sin `mes`, modelo desconocido |
| 503 | Modelo no cargado (arranque o falla de carga) |
| 500 | Error inesperado (queda en los logs y en `predict_requests_total{status="500"}`) |

### Ejemplos

**1. Solo el id del pozo (pronóstico del mes siguiente con `v2_notef`):**

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:8081/predict -ContentType "application/json" `
  -Body '{"idpozo": 166692}'
```

Respuesta real (GKE, 12/11/2026). El pozo 166692 es del holdout: el modelo no lo vio al entrenar.

```json
{
  "model": "v2_notef",
  "version": "v2_notef-20261112T001844",
  "use": "pronóstico: no usa información posterior al inicio del mes",
  "idpozo": 166692,
  "periodo": "2026-07",
  "prediccion_m3_mes": 2247.9,
  "fuera_de_rango": false,
  "categorias_no_vistas": [],
  "entrada": {"profundidad": 6641.0, "edad_meses": 21, "tipoextraccion": "Surgencia Natural",
              "empresa": "YPF S.A.", "areayacimiento": "LA ANGOSTURA SUR I"},
  "fuentes": ["pozo 166692 (dataset)"],
  "referencia_mae_holdout": 560.8
}
```

`fuentes` dice de dónde salió cada variable. Con `v1` o `v2` y sin `tef`, agrega `"tef supuesto = días del mes"`.

**2. Modelo descriptivo con TEF real del mes:**

```bash
curl -s -X POST http://localhost:8081/predict -H "Content-Type: application/json" \
  -d '{"idpozo": 166692, "model": "v2", "anio": 2026, "mes": 6, "tef": 28}'
```

**3. Pozo hipotético (todas las variables explícitas), como en la Figura 1 de la devolución:**

```json
{"model": "v2_notef", "anio": 2026, "mes": 3, "profundidad": 3000, "edad_meses": 6,
 "tipoextraccion": "Surgencia Natural", "empresa": "YPF S.A.", "areayacimiento": "LOMA CAMPANA-LLL"}
```

**4. Error 422 por falta de variables:**

```json
{"detail": "faltan variables para v2_notef: ['edad_meses', 'tipoextraccion', 'empresa', 'areayacimiento'] (envíelas o indique un idpozo)"}
```

## GET /wells/{idpozo}

Reproduce el "explorador de pozos" de la devolución: producción real mes a mes y la estimación de cada modelo vigente, para analizar la declinación. Respuesta real (k3d, 11/11/2026; resumida a 2 de los 6 meses):

```json
{
  "pozo": {"idpozo": 166692, "empresa": "YPF S.A.", "areayacimiento": "LA ANGOSTURA SUR I",
           "profundidad": 6641.0, "tipoextraccion": "Surgencia Natural",
           "primera_produccion": "2024-10", "ultimo_mes": "2026-06",
           "edad_censurada": false, "meses_con_datos": 6},
  "meses": [
    {"periodo": "2026-01", "prod_pet_real": 5664.0, "tef": 31.0, "tipoestado": "Extracción Efectiva",
     "estimado_v1": 3476.3, "estimado_v2": 4151.0, "estimado_v2_notef": 3386.6},
    {"periodo": "2026-06", "prod_pet_real": 4258.0, "tef": 30.0, "tipoestado": "Extracción Efectiva",
     "estimado_v1": 3476.3, "estimado_v2": 2967.8, "estimado_v2_notef": 2247.9}
  ],
  "versiones": {"v1": "v1-20261111T212728", "v2": "v2-20261111T212736",
                "v2_notef": "v2_notef-20261111T212741"}
}
```

La tabla completa de los 6 meses y su análisis están en `docs/resultados.md` §3.0.1.

## GET /models

Respuesta real (GKE, 12/11/2026; se muestra un modelo de los tres):

```json
{
  "default_model": "v2_notef",
  "store": "gs://unlu-ia-boyer-lab-511417-tfvm-artifacts/vm",
  "models": {
    "v2_notef": {
      "model": "v2_notef",
      "version": "v2_notef-20261112T001844",
      "use": "pronóstico: no usa información posterior al inicio del mes",
      "dataset": "vm_shale_202601_202606",
      "features": ["profundidad", "edad_meses", "tipoextraccion", "empresa", "areayacimiento"],
      "params": {"max_depth": 8, "min_samples_leaf": 5},
      "holdout": {"mae": 560.81, "rmse": 1065.45, "r2": 0.6346},
      "cv_mae": {"mean": 572.56, "std": 28.99},
      "loaded_utc": "2026-11-12T00:21:36+00:00"
    }
  },
  "errors": {},
  "wells_dataset": "vm_shale_202601_202606",
  "wells_count": 3278
}
```

## Métricas (`/metrics`)

| Métrica | Tipo | Etiquetas |
|---|---|---|
| `predict_requests_total` | counter | `model`, `status` |
| `predict_latency_seconds` | histogram | `model` |
| `predict_value_m3` | histogram | `model` |
| `predict_out_of_range_total` | counter | `model` |
| `model_info` | gauge (1) | `model`, `version` |
| `model_holdout_mae_m3` | gauge | `model` |
| `model_reload_total` | counter | `model`, `result` |
| `db_write_errors_total` | counter | — |
