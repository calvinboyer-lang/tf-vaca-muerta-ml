<#
.SYNOPSIS
  Atajos del Trabajo Final para PowerShell (reemplaza al Makefile del template).

.EXAMPLE
  .\tasks.ps1 help                       # lista de tareas
  .\tasks.ps1 setup                      # .venv + dependencias
  .\tasks.ps1 data-arcgis                # descarga producción + padrón + trayectorias
  .\tasks.ps1 data-build                 # snapshot versionado
  .\tasks.ps1 train-all                  # v1, v2 y v2_notef en .\artifacts
  .\tasks.ps1 k3d-up ; .\tasks.ps1 k3d-images ; .\tasks.ps1 k3d-deploy
  .\tasks.ps1 loadtest -Vus 50 -Duration 2m

  Si PowerShell bloquea el script:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#>
param(
    [Parameter(Position = 0)][string]$Task = "help",
    [ValidateSet("v1", "v2", "v2_notef")][string]$Model = "v2",
    [string]$Dataset = "",
    [string]$Store = ".\artifacts",
    [int[]]$Layers = @(9),
    [string]$Padron = "",
    [string]$Desde = "",
    [switch]$Tune,
    [string]$Tag = "local",
    [int]$Vus = 10,
    [string]$Duration = "2m",
    [string]$Label = "",
    [string]$LoadModel = "",
    [int]$Replicas = 0,
    [string]$Project = $env:GCP_PROJECT_ID,
    [string]$Zone = "us-central1-a",
    [string]$Cluster = "tfvm-dev-gke"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$K3dCluster = "tfvm"

function Require-Venv { if (-not (Test-Path $Py)) { throw "No existe .venv. Corré primero: .\tasks.ps1 setup" } }

function Latest-Snapshot {
    if ($Dataset) { return $Dataset }
    $snap = Get-ChildItem "data\snapshots\*.csv.gz" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $snap) { throw "No hay snapshot en data\snapshots. Corré data-arcgis y data-build (o pasá -Dataset)." }
    return $snap.FullName
}

function Run([string[]]$CmdArgs) {
    Write-Host "> python $($CmdArgs -join ' ')" -ForegroundColor Cyan
    & $Py @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "falló (código $LASTEXITCODE)" }
}

function Exec([string]$Exe, [string[]]$CmdArgs) {
    Write-Host "> $Exe $($CmdArgs -join ' ')" -ForegroundColor Cyan
    & $Exe @CmdArgs
    if ($LASTEXITCODE -ne 0) { throw "$Exe falló (código $LASTEXITCODE)" }
}

function Ensure-PostgresSecret {
    & kubectl get secret postgres-auth 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) {
        # Clave aleatoria generada en el momento: nunca se escribe en el repo.
        $bytes = New-Object byte[] 18
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $pass = [Convert]::ToBase64String($bytes) -replace '[+/=]', 'x'
        Exec kubectl @("create", "secret", "generic", "postgres-auth", "--from-literal=password=$pass")
    }
    else { Write-Host "secret postgres-auth ya existe" }
}

function Snapshot-Key {
    $name = Split-Path (Latest-Snapshot) -Leaf
    return "datasets/$name"
}

function Bucket {
    if (-not $Project) { throw "Falta -Project (o la variable de entorno GCP_PROJECT_ID)" }
    return "$Project-tfvm-artifacts"
}

switch ($Task) {
    "help" {
        @"
Local (VS Code)
  setup            crea .venv e instala requirements-dev.txt (+ kernel de Jupyter)
  test             pytest (vm_ml, entrenamiento, API)
  data-discover    lista capas/tablas del ArcGIS de la Secretaría de Energía
  data-arcgis      descarga producción (-Layers 9) + padrón (capa 0) + trayectorias (capa 10)
  data-build       arma el snapshot versionado    (-Padron archivo.csv, -Desde AAAA-MM)
  data-synthetic   snapshot SINTÉTICO para probar sin internet
  train            entrena una versión            (-Model v1|v2|v2_notef -Store ... -Tune)
  train-all        entrena v1, v2 y v2_notef      (-Store ... -Tune)
  versions         historial de versiones         (-Model ... -Store ...)
  api              levanta la API en http://localhost:8081 con -Store

k3d (Kubernetes local)
  k3d-up           crea el cluster '$K3dCluster' con .\artifacts montado en /artifacts
  k3d-images       docker build de api y training + import al cluster (-Tag)
  k3d-deploy       secret + postgres + api + training (CronJobs suspendidos)
  k3d-train        lanza el entrenamiento de -Model como Job en el cluster
  port-forward     http://localhost:8081 -> Service api
  k3d-down         borra el cluster

GKE
  gke-creds        kubectl apunta al cluster   (-Project -Zone -Cluster)
  upload-dataset   sube el snapshot al bucket gs://<Project>-tfvm-artifacts/vm/datasets/
  gke-train        lanza el entrenamiento de -Model como Job en GKE

Pruebas (k3d o GKE, según el contexto de kubectl)
  replicas         fija N réplicas sin HPA      (-Replicas 1|3|5) ; -Replicas 0 = vuelve al HPA
  loadtest         Job de k6 en el cluster      (-Vus 1|10|50|100 -Duration 2m -Label ...)
"@ | Write-Host
    }
    "setup" {
        if (-not (Test-Path $Py)) { python -m venv .venv }
        Run @("-m", "pip", "install", "--upgrade", "pip")
        Run @("-m", "pip", "install", "-r", "requirements-dev.txt")
        Run @("-m", "ipykernel", "install", "--user", "--name", "tf-vaca-muerta", "--display-name", "TF Vaca Muerta (.venv)")
    }
    "test" { Require-Venv; Run @("-m", "pytest", "-q", "tests") }
    "data-discover" { Require-Venv; Run @("scripts\data_vm.py", "discover") }
    "data-arcgis" {
        Require-Venv
        Run (@("scripts\data_vm.py", "arcgis", "--layers") + ($Layers | ForEach-Object { "$_" }))
    }
    "data-build" {
        Require-Venv
        $a = @("scripts\data_vm.py", "build")
        if ($Padron) { $a += @("--padron", $Padron) }
        if ($Desde) { $a += @("--desde", $Desde) }
        Run $a
    }
    "data-synthetic" { Require-Venv; Run @("scripts\data_vm.py", "synthetic") }
    "train" {
        Require-Venv
        $a = @("-m", "training.train", "--model", $Model, "--dataset", (Latest-Snapshot), "--store", $Store)
        if ($Tune) { $a += "--tune" }
        Run $a
    }
    "train-all" {
        Require-Venv
        $ds = Latest-Snapshot
        foreach ($m in @("v1", "v2", "v2_notef")) {
            $a = @("-m", "training.train", "--model", $m, "--dataset", $ds, "--store", $Store)
            if ($Tune) { $a += "--tune" }
            Run $a
        }
    }
    "versions" { Require-Venv; Run @("scripts\versions.py", "--model", $Model, "--store", $Store) }
    "api" {
        Require-Venv
        $env:STORE_URI = (Resolve-Path $Store).Path
        $env:RELOAD_INTERVAL_S = "30"
        Run @("-m", "uvicorn", "api.main:app", "--port", "8081")
    }

    "k3d-up" {
        New-Item -ItemType Directory -Force -Path "artifacts" | Out-Null
        $mount = (Resolve-Path "artifacts").Path + ":/artifacts@all"
        Exec k3d @("cluster", "create", $K3dCluster, "--agents", "1", "--volume", $mount, "--wait")
        Exec kubectl @("get", "nodes")
    }
    "k3d-images" {
        Exec docker @("build", "-f", "api/Dockerfile", "-t", "api:$Tag", ".")
        Exec docker @("build", "-f", "training/Dockerfile", "-t", "training:$Tag", ".")
        Exec k3d @("image", "import", "api:$Tag", "training:$Tag", "-c", $K3dCluster)
    }
    "k3d-deploy" {
        Ensure-PostgresSecret
        Exec helm @("upgrade", "--install", "postgres", "helm/charts/postgres", "--wait")
        Exec helm @("upgrade", "--install", "training", "helm/charts/training", "-f", "helm/charts/training/values-k3s.yaml",
            "--set", "image.tag=$Tag", "--set", "dataset=$(Snapshot-Key)")
        Exec helm @("upgrade", "--install", "api", "helm/charts/api", "-f", "helm/charts/api/values-k3s.yaml",
            "--set", "image.tag=$Tag", "--wait", "--timeout", "3m")
        Exec kubectl @("get", "pods,svc,hpa,cronjob,pvc")
    }
    "k3d-train" {
        $job = "train-$($Model -replace '_','-')-$(Get-Date -Format 'MMddHHmmss')"
        Exec kubectl @("create", "job", $job, "--from=cronjob/training-$($Model -replace '_','-')")
        Exec kubectl @("wait", "--for=condition=complete", "job/$job", "--timeout=15m")
        Exec kubectl @("logs", "job/$job")
    }
    "port-forward" { Exec kubectl @("port-forward", "svc/api", "8081:8081") }
    "k3d-down" { Exec k3d @("cluster", "delete", $K3dCluster) }

    "gke-creds" {
        if (-not $Project) { throw "Falta -Project" }
        Exec gcloud @("container", "clusters", "get-credentials", $Cluster, "--zone", $Zone, "--project", $Project)
    }
    "upload-dataset" {
        $snap = Latest-Snapshot
        $manifest = $snap -replace '\.csv\.gz$', '.manifest.json'
        $dest = "gs://$(Bucket)/vm/datasets/"
        Exec gcloud @("storage", "cp", $snap, $manifest, $dest)
        Write-Host "Dataset en el store: $(Snapshot-Key)"
    }
    "gke-train" {
        $job = "train-$($Model -replace '_','-')-$(Get-Date -Format 'MMddHHmmss')"
        Exec kubectl @("create", "job", $job, "--from=cronjob/training-$($Model -replace '_','-')")
        Exec kubectl @("wait", "--for=condition=complete", "job/$job", "--timeout=20m")
        Exec kubectl @("logs", "job/$job")
    }

    "replicas" {
        if ($Replicas -gt 0) {
            # Réplicas fijas: se quita el HPA para que no las cambie durante la prueba.
            & kubectl delete hpa api --ignore-not-found | Out-Null
            Exec kubectl @("scale", "deployment/api", "--replicas=$Replicas")
            Exec kubectl @("rollout", "status", "deployment/api", "--timeout=5m")
        }
        else {
            Write-Host "Volvé a instalar el chart (k3d-deploy o deploy.yml) para restaurar el HPA." -ForegroundColor Yellow
        }
        Exec kubectl @("get", "deploy/api", "-o", "wide")
    }
    "loadtest" {
        if (-not $Label) { $Label = "vus$Vus-$(Get-Date -Format 'HHmmss')" }
        $Label = ($Label.ToLower() -replace '[^a-z0-9-]', '-')
        $yaml = (Get-Content "loadtest/k6-job.yaml" -Raw) `
            -replace '__LABEL__', $Label -replace '__VUS__', "$Vus" `
            -replace '__DURATION__', $Duration -replace '__MODEL__', $LoadModel
        & kubectl create configmap k6-script --from-file=loadtest/k6-predict.js --dry-run=client -o yaml | kubectl apply -f -
        & kubectl delete job "k6-$Label" --ignore-not-found | Out-Null
        $yaml | kubectl apply -f -
        Write-Host "Esperando el Job k6-$Label (en otra terminal: kubectl get hpa -w)..." -ForegroundColor Yellow
        Exec kubectl @("wait", "--for=condition=complete", "job/k6-$Label", "--timeout=30m")
        $log = & kubectl logs "job/k6-$Label"
        $log | Select-String -Pattern "RESULT" | ForEach-Object { $_.Line }
        New-Item -ItemType Directory -Force -Path "results" | Out-Null
        $ctx = (& kubectl config current-context) -replace '[^A-Za-z0-9-]', '_'
        $log | Out-File -Encoding utf8 "results\k6-$ctx-$Label.log"
        Write-Host "Log guardado en results\k6-$ctx-$Label.log"
    }
    default { throw "Tarea desconocida: $Task (.\tasks.ps1 help)" }
}
