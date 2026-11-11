# Resultados de las pruebas y análisis

> **Estado:** plantilla. Se completa con mediciones REALES (salidas de `train`, de `versions`, líneas `RESULT` de k6 y capturas de Grafana). No se escriben números que no hayan salido de una corrida.

## 1. Arquitectura efectivamente usada

| Elemento | Valor |
|---|---|
| Proyecto / región / zona | `<proyecto>` / us-central1 / us-central1-a |
| Cluster | `tfvm-dev-gke`, zonal, canal REGULAR, versión `<kubectl version>` |
| Node pool | e2-standard-2 (2 vCPU, 8 GB), disco 40 GB pd-standard, autoescalado 1–4 (nodos al inicio: `<n>`) |
| Servicios desplegados | api (Deployment + HPA), postgres (StatefulSet + PVC 2 Gi), training (3 CronJobs), kube-prometheus-stack, Loki, Alloy |
| Recursos por pod de la API | requests 250m / 384Mi, limits 1 CPU / 768Mi, 1 proceso uvicorn |
| HPA | 2–10 réplicas, CPU 60 % de la request, estabilización 0 s al subir y 120 s al bajar |
| Modelo | `DecisionTreeRegressor` (scikit-learn 1.5.2) en un pipeline con one-hot; artefacto joblib de `<KB>` |
| Dónde se entrenó | `<local (PC) / Job en GKE>` |
| Dataset | `<dataset_id>`, sha256 `<…>`, `<filas>` filas / `<pozos>` pozos, meses `<desde>`–`<hasta>` |

## 2. Ciclo de entrenamiento

### 2.1 Datos y respuesta a la devolución (punto 1)

Configuración original (notebook, sección 3.1): `DecisionTreeRegressor(max_depth=6, min_samples_leaf=20)`, `GroupShuffleSplit(test_size=0.2, random_state=42)` por `idpozo`.

| Separación | MAE | RMSE | R² |
|---|---:|---:|---:|
| Por pozo, semilla 42 (original) | | | |
| Por pozo, árbol sin límites | | | |
| Aleatoria por fila | | | |

### 2.2 Validación repetida (10 semillas, conjunto de desarrollo)

| Versión | Modelo | MAE (m³/mes) | RMSE | R² |
|---|---|---|---|---|
| v1 | promedio | | | |
| v1 | lineal | | | |
| v1 | árbol | | | |
| v2 | árbol | | | |
| v2_notef | árbol | | | |

### 2.3 Holdout fijo y validación temporal

| Versión | MAE holdout | R² holdout | MAE train | Temporal (mismos pozos) | Temporal (pozos nuevos) |
|---|---:|---:|---:|---:|---:|
| v1 | | | | | |
| v2 | | | | | |
| v2_notef | | | | | |

### 2.4 Versiones, tiempos y promoción

Salida de `.\tasks.ps1 versions -Model <m>` y `GET /stats` (tabla `training_runs`):

| Versión | Dataset | Parámetros | MAE holdout | CV MAE ± desvío | Fit (s) | Total (s) | ¿Promovida? | Motivo |
|---|---|---|---:|---:|---:|---:|---|---|

Experimento que pide la devolución (punto 4): se reentrena la **misma** configuración y se verifica que **no** se promueve (mejora 0 % < 1 %). Después se reentrena con `--tune` y se documenta la decisión con el intervalo bootstrap.

### 2.5 Declinación (punto 3)

Captura de `GET /wells/{idpozo}` o del gráfico del notebook (sección 4), con un análisis de por qué el árbol queda casi plano.

## 3. Servicio de inferencia

### 3.1 Metodología

- k6 dentro del cluster (`loadtest/k6-job.yaml`): la carga llega al Service y se reparte entre las réplicas.
- Carga **cerrada**: N usuarios virtuales sin pausa, es decir, N peticiones concurrentes. Subida de 15 s y meseta de 2 min.
- Cada petición es `POST /predict {"idpozo": <al azar entre 500 pozos>}` con el modelo por defecto (`v2_notef`).
- Réplicas fijas: `.\tasks.ps1 replicas -Replicas N` (quita el HPA). Autoescalado: reinstalar el chart (HPA 2–10).
- Por corrida se guarda `results\k6-<contexto>-<label>.log` y una captura del dashboard.

Comandos de la matriz:

```powershell
foreach ($r in 1,3,5) {
  .\tasks.ps1 replicas -Replicas $r
  foreach ($v in 1,10,50,100) { .\tasks.ps1 loadtest -Vus $v -Duration 2m -Label "r$r-vus$v" }
}
# HPA 2-10: reinstalar el chart api (deploy.yml con charts=api) y luego
foreach ($v in 1,10,50,100,150) { .\tasks.ps1 loadtest -Vus $v -Duration 3m -Label "hpa-vus$v" }
```

### 3.2 Réplicas fijas

| Réplicas | VUs | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Errores | CPU por pod (Grafana) |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | 1 | | | | | | |
| 1 | 10 | | | | | | |
| 1 | 50 | | | | | | |
| 1 | 100 | | | | | | |
| 3 | 1 … 100 | | | | | | |
| 5 | 1 … 100 | | | | | | |

### 3.3 Autoescalado (HPA 2–10)

| VUs | RPS | p95 (ms) | Errores | Réplicas máx. | Tiempo hasta escalar | Nodos máx. |
|---:|---:|---:|---:|---:|---|---:|

Captura del panel "Réplicas de la API (HPA)" junto a "Latencia de /predict" durante la rampa.

### 3.4 Análisis (a redactar con los datos)

- **Saturación de una réplica:** a partir de cuántas peticiones por segundo la latencia crece por espera en cola (el docente midió unas 200 rps por proceso en su prueba local).
- **Escalado lineal:** RPS con 3 y 5 réplicas contra 1 réplica. Si no escala, buscar el cuello de botella (CPU de los nodos, k6, red).
- **Justificación del HPA:** umbral del 60 % y request de 250m frente al consumo medido. Ajustar si el HPA reacciona tarde o de más.
- **Alerta:** si `PredictLatenciaP95Alta` (p95 > 300 ms durante 3 min) se disparó, en qué condiciones; si no, umbral frente a los p95 observados.
- **Errores:** origen (timeouts, 503 durante el arranque de pods, etc.).
