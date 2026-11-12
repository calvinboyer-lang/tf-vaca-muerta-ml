# Evidencias (capturas)

**Anexo de evidencias.** El informe del trabajo final mantiene la estructura de la consigna (arquitectura y decisiones de diseño, instrucciones de despliegue, especificación de endpoints, resultados de pruebas con análisis y reflexión crítica). Estas capturas se **agregan** como respaldo de lo ejecutado; no reemplazan ni reordenan esas secciones. Para cubrir como mínimo las pantallas del Laboratorio 8 se agrupan con la misma lógica (CI → Terraform/deploy → pods y `/predict` → Grafana base → carga y HPA → estado final), más evidencias propias del TF (datos, entrenamiento, k3d).

Criterios (indicaciones del docente):

- El **comando debe quedar visible** arriba de la salida (en el lab, la 3.1 mostraba la tabla de pods sin el `kubectl get pods`).
- Si hay **pods en `Pending`**, capturar `kubectl describe pod <pod>`: los eventos (`FailedScheduling`, `Insufficient cpu`, …) dicen si falta CPU en los nodos o es otra causa.
- Si se genera tráfico manual para que los paneles tengan datos, aclararlo en el epígrafe (no es la carga de k6).

Estado: ✅ capturada · 🔁 conviene repetirla (comando tapado o incompleta) · ⏳ pendiente.

## 1. Datos y entrenamiento local (PC, Windows)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 1.1 | `capturas/1.1_pytest_24_passed.png` | `.\tasks.ps1 test`: 24 tests pasados | ✅ |
| 1.2 | `capturas/1.2_arcgis_trayectorias.png` | Descarga de la capa 10 (trayectorias): 2.042 filas para 3.278 pozos | ✅ |
| 1.3 | `capturas/1.3_train_all_tune_pc.png` | `train-all -Tune`: v2_notef MAE 560,8, R² 0,635, promovida | ✅ |

## 2. Kubernetes local (k3d)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 2.1 | `capturas/2.1_herramientas_k8s.png` | Docker 29.9.3, k3d 5.9.0, Helm 4.3.1, kubectl 1.36.1 | ✅ |
| 2.2 | `capturas/2.2_k3d_nodos_ready.png` | Cluster `tfvm`: server y agent `Ready` | ✅ |
| 2.3a | `capturas/2.3a_k3d_deploy_svc_hpa_cronjob_pvc.png` | Services, HPA, 3 CronJobs suspendidos, PVC `Bound` | ✅ |
| 2.3b | `capturas/2.3b_k3d_pods_running.png` | `api` y `postgres-0` en `Running` | 🔁 sin el comando visible |
| 2.4 | `capturas/2.4_k3d_entrenamiento_no_promovido.png` | Job en k3d: mismas métricas, no promovido, run en PostgreSQL | ✅ |
| 2.5a | `capturas/2.5a_k3d_k6_vus10.png` | k6 en el cluster: 100,7 req/s, p95 168 ms, 0 % errores | ✅ |
| 2.5b | `capturas/2.5b_k3d_hpa_1a4_y_stats_postgres.png` | HPA 1 → 4 réplicas y `/stats` con 7.568 predicciones en PostgreSQL | ✅ |

## 3. CI (equivalente al entregable 1.1 del lab)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 3.1 | `capturas/3.1_ci_15_success.png` | `ci` #15 (commit `14d68a6`) en Success: test, infra, build, publish (api) y publish (training) | ✅ |
| 3.2 | — | Log de **publish (api)** con el título del job y la línea `pushed` con el SHA completo | ⏳ |

## 4. GCP y Terraform (equivalente al 2.1)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 4.1a | `capturas/4.1a_gcp_recursos_lab.png` | Bucket de tfstate, pool `github-pool` y `ci-deployer` existentes; sin clusters GKE prendidos | ✅ |
| 4.1b | `capturas/4.1b_gcp_provider_oidc.png` | Provider OIDC `github-provider-tfvm` creado | ✅ |
| 4.1c | `capturas/4.1c_gcp_roles_ci_deployer.png` | Roles de `ci-deployer` y binding de Workload Identity del repo | ✅ |
| 4.1d | `capturas/4.1d_github_variables.png` | Las 5 variables de GitHub Actions | ✅ |
| 4.2 | `capturas/4.2_deploy_terraform_success.png` | `deploy` #1: terraform 11 min 9 s y helm en Success | ✅ |
| 4.2b | — | Log de **terraform apply** con `Apply complete! Resources: N added…` | ⏳ (se saca del run #1 en GitHub) |
| 4.3 | `capturas/4.3_upload_dataset_bucket.png` | Snapshot y manifest subidos al bucket | ✅ |

## 5. GKE: entrenamiento, pods y `/predict` (equivalente al 3.1)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 5.1 | `capturas/5.1_gke_creds_nodos.png` | Contexto GKE y nodo `Ready` (v1.36.4-gke.2377000) | ✅ |
| 5.2a | `capturas/5.2a_gke_train_v1.png` | Job de entrenamiento v1 en GKE: lee/escribe el bucket, promovida | ✅ |
| 5.2b | `capturas/5.2b_gke_train_v2_v2notef.png` | v2 y v2_notef entrenadas y promovidas en GKE | ✅ |
| 5.3 | `capturas/5.3_deploy_api_observability.png` | `deploy` #3: helm y observability en Success | ✅ |
| 5.3b | `capturas/5.3b_deploy5_api_700m.png` | `deploy` #5: API con requests de 700m (imagen `2565ebb`) | ✅ |
| 5.4 | `capturas/5.4_gke_pods_hpa_observability.png` | Pods de la API, PostgreSQL, HPA 2/10 y stack de observabilidad | 🔁 el comando queda cortado arriba |
| 5.5 | `capturas/5.5_gke_pods_y_predict.png` | Comando visible, encabezado de la tabla, 2 réplicas de la API y `postgres-0` en `Running` con su nodo; `/predict` responde `v2_notef-20261112T001844` (2.247,9 m³) | ✅ |

## 6. Grafana base, sin carga (equivalente al 4.1)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 6.1 | `capturas/6.1_grafana_base_sin_carga.png` | Dashboard "TF Vaca Muerta – API de inferencia" con 2 réplicas, CPU por pod; tráfico mínimo (2 consultas manuales, no k6) | ✅ |
| 6.2 | `capturas/6.2_grafana_version_por_replica_y_logs_loki.png` | Tabla "Versión cargada por réplica" (trazabilidad modelo/pod/versión/MAE) y logs de la API en Loki (apagado de réplicas al bajar el HPA) | ✅ |
| 6.3 | `capturas/6.3_grafana_alertas_tfvm_api.png` | Grupo `tfvm-api` con las 5 reglas cargadas en Prometheus (evaluación cada 30 s), todas en estado normal | ✅ |

## 7. Carga y escalado (equivalente al 5.1 y 5.2)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 7.1 | `capturas/7.1_grafana_r1_saturacion.png` | 1 réplica: techo ≈ 60 req/s, CPU ≈ 0,9 núcleos, bache de disponibles a 0 | ✅ |
| 7.2a | `capturas/7.2a_eventos_sonda_reinicio.png` | Eventos: liveness con timeout → reinicio del pod; Job k6 con backoff | ✅ |
| 7.2b | `capturas/7.2b_pods_y_k6_r1_vus50_errores.png` | `RESTARTS 1` y log de k6 con `i/o timeout`: 52,7 % de errores | ✅ |
| 7.3a | `capturas/7.3a_k6_r1_vus1.png` | 1 réplica, 1 VU: 62,2 req/s | ✅ |
| 7.3b | `capturas/7.3b_k6_r1b_sondas_corregidas.png` | 1 réplica, 1–100 VUs con sondas corregidas: 0 % errores | ✅ |
| 7.4a | `capturas/7.4a_grafana_r3_un_nodo.png` | 3 réplicas en un nodo: ≈ 0,43 núcleos por pod, latencia peor | ✅ |
| 7.4b | `capturas/7.4b_k6_r3_resultados.png` | Resultados k6 de 3 réplicas (requests 250m) | ✅ |
| 7.4c | — | `kubectl get pods -o wide` mostrando las 3 réplicas en el mismo nodo | ⏳ (se reemplaza por la prueba con 700m) |
| 7.5 | — | 3 y 5 réplicas con requests de 700m: `get pods -o wide` (nodos) + resultados | ⏳ |
| 7.6a | `capturas/7.6a_hpa_5_replicas_nodos_nuevos.png` | Prueba con keep-alive: HPA en 5 réplicas y 2 nodos nuevos creados por GKE | ✅ |
| 7.6b | `capturas/7.6b_hpa_eventos_rescale.png` | `kubectl describe hpa api`: eventos `SuccessfulRescale` de todas las pruebas (2 → 5 → 3 → 9 → 3 → 5 → 6 → 8 → 10 → 4 → 2) con su antigüedad y la política de subida (4 pods o 100 % cada 15 s) | ✅ |
| 7.7a | `capturas/7.7a_grafana_hpa_vus150_keepalive.png` | HPA con keep-alive: 5 réplicas pero el tráfico queda en ~130 req/s; las réplicas nuevas casi sin CPU | ✅ |
| 7.7b | `capturas/7.7b_k6_hpa_vus150_keepalive.png` | RESULT: 131,3 req/s, p95 1.520 ms, 0 % errores | ✅ |
| 7.9a | `capturas/7.9a_grafana_hpa_vus150_noreuse.png` | Primer intento sin keep-alive (interrumpido al relanzar el comando): HPA pide 9, 7 disponibles | ✅ |
| 7.9b1–3 | `capturas/7.9b1…3_*.png` | 4 réplicas `Pending`: `Insufficient cpu` y `max node group size reached`; HPA en 10 | ✅ |
| 7.9c | `capturas/7.9c_k6_hpa_noreuse_resultado.png` | RESULT sin keep-alive: 247,7 req/s, p50 255 ms, p95 1.962 ms, 0 % errores | ✅ |
| 7.9d | `capturas/7.9d_grafana_hpa_noreuse_b.png` | Dashboard: deseadas 10, disponibles 7, pico 295 req/s, CPU repartida entre réplicas | ✅ |
| 7.8 | `capturas/7.8_describe_pod_pending_k6_insufficient_cpu.png` | Pod de k6 en `Pending` 2,5 min: `FailedScheduling` *0/4 nodes are available: 4 Insufficient cpu* y `NotTriggerScaleUp` *max node group size reached* (4 nodos, el máximo); se programó cuando el HPA bajó réplicas y liberó CPU | ✅ |

## 8. Estado final (equivalente al 6.1)

| # | Archivo | Qué muestra | Estado |
|---|---|---|---|
| 8.1 | `capturas/8.1_pausa_nodos_cero.png` | Pausa nocturna: sin nodos de GKE corriendo | ✅ |
| 8.2 | — | `helm list -A` vacío / réplica mínima | ⏳ |
| 8.3 | — | Consola de GCP: clusters y costos al cierre | ⏳ |
