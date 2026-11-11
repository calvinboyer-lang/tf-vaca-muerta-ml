<#
.SYNOPSIS
  Atajos del Trabajo Final para PowerShell (reemplaza al Makefile del template).

.EXAMPLE
  .\tasks.ps1 help
  .\tasks.ps1 setup
  .\tasks.ps1 data-discover
  .\tasks.ps1 data-arcgis -Layers 9
  .\tasks.ps1 data-build
  .\tasks.ps1 train -Model v2
  .\tasks.ps1 train-all -Tune

  Si PowerShell bloquea el script:
    Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
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
    [string]$Tag = "local"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$Py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

function Require-Venv {
    if (-not (Test-Path $Py)) { throw "No existe .venv. Corré primero: .\tasks.ps1 setup" }
}

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

switch ($Task) {
    "help" {
        @"
Tareas:
  setup            crea .venv e instala requirements-dev.txt
  test             pytest (vm_ml + entrenamiento)
  data-discover    lista capas/tablas del ArcGIS de la Secretaría de Energía
  data-arcgis      descarga Vaca Muerta shale     (-Layers 9 10 ...)
  data-build       arma el snapshot versionado    (-Padron archivo.csv, -Desde AAAA-MM)
  data-synthetic   snapshot SINTÉTICO para probar sin internet
  train            entrena una versión            (-Model v1|v2|v2_notef -Store ... -Tune)
  train-all        entrena v1, v2 y v2_notef      (-Store ... -Tune)
  versions         lista versiones y la vigente   (-Model ... -Store ...)
  build-training   docker build de la imagen de entrenamiento (-Tag)
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
        $layerArgs = $Layers | ForEach-Object { "$_" }
        Run (@("scripts\data_vm.py", "arcgis", "--layers") + $layerArgs)
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
    "build-training" { docker build -f training/Dockerfile -t "training:$Tag" . }
    default { throw "Tarea desconocida: $Task (.\tasks.ps1 help)" }
}
