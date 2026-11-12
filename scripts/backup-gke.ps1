<#
.SYNOPSIS
  Respaldo de lo que vive SOLO en GCP/GKE y no se puede regenerar, antes de
  apagar o eliminar el cluster. Escribe todo en .\backup\gke-AAAAMMDD-HHMM\.

  1. Bucket de artefactos (gs://<proyecto>-tfvm-artifacts/vm): versiones de los
     modelos (model.joblib + metadata.json), punteros current.json y dataset.
  2. PostgreSQL: training_runs completa, predictions completa (csv.gz) y un
     resumen de predictions por modelo, versión y pod.
  3. Prometheus: series de los paneles del dashboard (JSON de query_range),
     porque su almacenamiento se pierde al borrar el cluster.
  4. Estado de Kubernetes en texto: eventos del HPA, eventos del namespace,
     nodos, pods, releases de Helm.

  Uso (desde la carpeta del repo, con kubectl apuntando a GKE):
    .\scripts\backup-gke.ps1
    .\scripts\backup-gke.ps1 -Desde "2026-11-12T21:00:00Z"
#>
param(
    [string]$Project = "unlu-ia-boyer-lab-511417",
    # Inicio de la ventana de métricas a exportar (UTC). Por defecto, 12 h atrás.
    [string]$Desde = "",
    [int]$StepSeconds = 15
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$dest = "backup\gke-" + (Get-Date -Format "yyyyMMdd-HHmm")
New-Item -ItemType Directory -Force -Path "$dest\bucket", "$dest\postgres", "$dest\prometheus", "$dest\k8s" | Out-Null
Write-Host "Destino: $dest" -ForegroundColor Cyan

function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) { throw "falló: $exe $($argv -join ' ')" }
}
function Save-Text([string]$path, [string[]]$argv) {
    # Salida de kubectl/helm a un archivo UTF-8 (PowerShell 5.1 usa UTF-16 con '>').
    $ErrorActionPreference = "Continue"   # avisos en stderr no cortan el respaldo
    $out = & $argv[0] @($argv[1..($argv.Count - 1)]) 2>&1
    $out | Out-File -Encoding utf8 $path
}

# ---- 1. Bucket ---------------------------------------------------------------
Write-Host "`n[1/4] Bucket de artefactos" -ForegroundColor Yellow
Run gcloud @("storage", "cp", "-r", "gs://$Project-tfvm-artifacts/vm", "$dest\bucket")
Get-ChildItem -Recurse "$dest\bucket" -Filter "current.json" | ForEach-Object { Write-Host "  $($_.FullName.Replace((Get-Location).Path + '\', ''))" }

# ---- 2. PostgreSQL -----------------------------------------------------------
Write-Host "`n[2/4] PostgreSQL" -ForegroundColor Yellow
$queries = [ordered]@{
    "training_runs"       = "SELECT * FROM training_runs ORDER BY id"
    "predictions_resumen" = "SELECT model, version, pod, count(*) AS n, min(ts_utc) AS desde, max(ts_utc) AS hasta, round(avg(latency_ms)::numeric, 2) AS latencia_media_ms, round((percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms))::numeric, 2) AS latencia_p95_ms, sum(CASE WHEN status_code >= 500 THEN 1 ELSE 0 END) AS errores_5xx, sum(CASE WHEN out_of_range THEN 1 ELSE 0 END) AS fuera_de_rango FROM predictions GROUP BY model, version, pod ORDER BY desde"
    "predictions"         = "SELECT * FROM predictions ORDER BY id"
}
foreach ($name in $queries.Keys) {
    $sql = "\copy ($($queries[$name])) TO '/tmp/$name.csv' CSV HEADER"
    Run kubectl @("exec", "postgres-0", "--", "psql", "-U", "vm", "-d", "vm", "-c", $sql)
}
Run kubectl @("exec", "postgres-0", "--", "gzip", "-f", "/tmp/predictions.csv")
foreach ($f in @("training_runs.csv", "predictions_resumen.csv", "predictions.csv.gz")) {
    # kubectl cp necesita rutas relativas en Windows (la ':' de 'C:' se confunde con pod:ruta).
    Run kubectl @("cp", "postgres-0:/tmp/$f", ($dest -replace '\\', '/') + "/postgres/$f")
}
Run kubectl @("exec", "postgres-0", "--", "rm", "-f", "/tmp/training_runs.csv", "/tmp/predictions_resumen.csv", "/tmp/predictions.csv.gz")

# ---- 3. Prometheus -----------------------------------------------------------
Write-Host "`n[3/4] Prometheus (series del dashboard)" -ForegroundColor Yellow
$end = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$start = if ($Desde) { [DateTimeOffset]::Parse($Desde).ToUnixTimeSeconds() } else { $end - 12 * 3600 }
$prom = (& kubectl -n observability get pods -l app.kubernetes.io/name=prometheus -o jsonpath="{.items[0].metadata.name}")
$series = [ordered]@{
    "rps_por_status"      = 'sum by (status) (rate(predict_requests_total[1m]))'
    "latencia_p50_s"      = 'histogram_quantile(0.50, sum by (le) (rate(predict_latency_seconds_bucket[1m])))'
    "latencia_p95_s"      = 'histogram_quantile(0.95, sum by (le) (rate(predict_latency_seconds_bucket[1m])))'
    "latencia_p99_s"      = 'histogram_quantile(0.99, sum by (le) (rate(predict_latency_seconds_bucket[1m])))'
    "replicas_deseadas"   = 'kube_horizontalpodautoscaler_status_desired_replicas{horizontalpodautoscaler="api"}'
    "replicas_disponibles"= 'kube_deployment_status_replicas_available{deployment="api"}'
    "cpu_por_pod"         = 'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="default",pod=~"api-.*",container="api"}[1m]))'
    "memoria_por_pod"     = 'sum by (pod) (container_memory_working_set_bytes{namespace="default",pod=~"api-.*",container="api"})'
    "cpu_nodos"           = 'sum by (instance) (rate(node_cpu_seconds_total{mode!="idle"}[1m]))'
    "pods_pending"        = 'sum(kube_pod_status_phase{namespace="default",phase="Pending"})'
    "nodos"               = 'count(kube_node_info)'
}
foreach ($name in $series.Keys) {
    $body = "query=" + [uri]::EscapeDataString($series[$name]) + "&start=$start&end=$end&step=$StepSeconds"
    $json = & kubectl -n observability exec $prom -c prometheus -- wget -qO- --post-data $body "http://localhost:9090/api/v1/query_range"
    if ($LASTEXITCODE -ne 0) { Write-Host "  $($name): falló (se sigue)" -ForegroundColor Red; continue }
    $json | Out-File -Encoding utf8 "$dest\prometheus\$name.json"
    Write-Host "  $name"
}

# ---- 4. Estado de Kubernetes -------------------------------------------------
Write-Host "`n[4/4] Estado de Kubernetes" -ForegroundColor Yellow
Save-Text "$dest\k8s\hpa_describe.txt"    @("kubectl", "describe", "hpa", "api")
Save-Text "$dest\k8s\eventos_default.txt" @("kubectl", "get", "events", "--sort-by=.lastTimestamp")
Save-Text "$dest\k8s\nodos.txt"           @("kubectl", "get", "nodes", "-o", "wide")
Save-Text "$dest\k8s\pods.txt"            @("kubectl", "get", "pods", "-A", "-o", "wide")
Save-Text "$dest\k8s\recursos.txt"        @("kubectl", "get", "deploy,sts,svc,hpa,cronjob,pvc", "-o", "wide")
Save-Text "$dest\k8s\helm_list.txt"       @("helm", "list", "-A")

Write-Host "`nListo. Contenido:" -ForegroundColor Green
Get-ChildItem -Recurse -File $dest | ForEach-Object {
    "{0,10:N0} KB  {1}" -f ($_.Length / 1KB), $_.FullName.Replace((Get-Location).Path + '\', '')
}
