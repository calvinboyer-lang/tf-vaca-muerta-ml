# TF Vaca Muerta ML

**Estimación de la producción mensual de petróleo de pozos shale de Vaca Muerta con Machine Learning, desplegada en Google Cloud.**
Trabajo Final — Diplomatura en IA, curso Infraestructura para IA (Calvin Boyer, trabajo individual).

Base: el repo del Laboratorio 8 (instancia de `infra-cloud-template`), con el ejemplo Iris reemplazado por un caso propio.

## Estado

| Fase | Contenido | Estado |
|---|---|---|
| 1 | Datos versionados, edad del pozo, 3 versiones del modelo, validación, servicio de entrenamiento, notebook | ✅ código listo · ⏳ correr con datos reales |
| 2 | API FastAPI (`/predict`, `/healthz`, `/metrics`, consultas), PostgreSQL, k3d local, k6 local | ⏳ |
| 3 | GitHub Actions + OIDC, Terraform (GKE e2-standard-2, bucket, IAM), despliegue | ⏳ |
| 4 | Prometheus, Grafana, Loki/Alloy, alerta, pruebas de carga (1/3/5 réplicas, HPA 2–10) | ⏳ |
| 5 | Documentación e informe | ⏳ |

## Estructura

```
vm_ml/            código compartido: datos, variables, evaluación, registro de modelos, almacenamiento
training/         servicio de entrenamiento (CLI + Dockerfile) -> model.joblib + metadata.json
scripts/          data_vm.py (descarga y snapshot), versions.py (historial de versiones)
data/snapshots/   snapshot versionado del dataset (csv.gz + manifest con sha256)
notebooks/        00_colab_original.ipynb (entrega previa) y 01_tf_vaca_muerta.ipynb (VS Code)
tests/            pytest con datos sintéticos
tasks.ps1         atajos para PowerShell (equivalente al Makefile del template)
```

## Primeros pasos (Windows, PowerShell, VS Code)

```powershell
git clone https://github.com/calvinboyer-lang/tf-vaca-muerta-ml.git
cd tf-vaca-muerta-ml
.\tasks.ps1 setup            # .venv + dependencias + kernel de Jupyter
.\tasks.ps1 test             # tests con datos sintéticos

.\tasks.ps1 data-discover    # capas y tablas del ArcGIS de la Secretaría de Energía
.\tasks.ps1 data-arcgis -Layers 9
.\tasks.ps1 data-build       # data\snapshots\vm_shale_AAAAMM_AAAAMM.csv.gz + manifest

.\tasks.ps1 train-all        # v1, v2 y v2_notef en .\artifacts
.\tasks.ps1 versions -Model v2
```

## Decisiones principales (resumen)

- **Snapshot versionado:** cada entrenamiento referencia el dataset por id y sha256 (`metadata.json`).
- **Edad del pozo** en lugar del mes del año. Fuente: padrón oficial si está disponible; si no, el primer mes con producción en la historia descargada (se marca la edad *censurada*).
- **Tres versiones:** `v1` (línea base de la propuesta), `v2` (+ edad, operadora y área; descriptiva, usa el TEF del mes) y `v2_notef` (sin TEF; la única válida para pronosticar).
- **Holdout fijo por pozo** (`sha256(salt:idpozo) % 100 < 20`): todas las versiones se comparan contra los mismos pozos.
- **Validación repetida** (10 semillas, promedio ± desvío) y **validación temporal** (últimos 2 meses).
- **Promoción:** la versión nueva y la vigente se evalúan sobre el mismo holdout. Se promueve si el MAE mejora ≥ 1 % y el intervalo bootstrap (por pozo, 90 %) de la mejora excluye el cero.
- **Seguridad del artefacto:** la API solo carga un `model.joblib` cuyo sha256 coincide con `current.json`; en GCP solo la cuenta de entrenamiento escribe en el bucket.

> Las estimaciones son académicas: no son pronósticos industriales validados ni reemplazan un análisis de ingeniería de reservorios.
