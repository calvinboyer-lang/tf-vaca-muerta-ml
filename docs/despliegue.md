# Instrucciones de despliegue (reproducibles)

Windows + PowerShell en VS Code. Desde la raíz del repo. Los comandos se corren **de a uno**, revisando la salida.

## Requisitos

| Herramienta | Para qué | Verificación |
|---|---|---|
| Python 3.12 | datos, entrenamiento, notebook | `python --version` |
| Docker Desktop | imágenes y k3d | `docker version` |
| k3d, kubectl, helm | Kubernetes local | `k3d version`, `kubectl version --client`, `helm version` |
| gcloud | GCP (bootstrap, bucket, credenciales) | `gcloud --version` |
| git | repo | `git --version` |

Si PowerShell bloquea los scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

---

## Fase 1. Datos y entrenamiento local

```powershell
git clone https://github.com/calvinboyer-lang/tf-vaca-muerta-ml.git
cd tf-vaca-muerta-ml
.\tasks.ps1 setup          # .venv + dependencias + kernel "TF Vaca Muerta (.venv)"
.\tasks.ps1 test           # tests con datos sintéticos
.\tasks.ps1 data-arcgis    # producción (tabla 9) + padrón (capa 0) + trayectorias (capa 10) -> data\raw
.\tasks.ps1 data-build     # data\snapshots\vm_shale_AAAAMM_AAAAMM.csv.gz + .manifest.json
.\tasks.ps1 train-all      # v1, v2, v2_notef -> .\artifacts
.\tasks.ps1 versions -Model v2_notef
```

Después se abre `notebooks/01_tf_vaca_muerta.ipynb` con el kernel **TF Vaca Muerta (.venv)** y se ejecuta todo.

El snapshot se versiona en git:

```powershell
git add data/snapshots
git commit -m "datos: snapshot vm_shale_AAAAMM_AAAAMM"
git push
```

## Fase 2. Kubernetes local (k3d)

Requiere haber corrido `train-all`, porque los pods leen `.\artifacts` montado como `/artifacts`.

```powershell
.\tasks.ps1 k3d-up          # cluster "tfvm" (1 server + 1 agent)
.\tasks.ps1 k3d-images      # docker build api/training + k3d image import
.\tasks.ps1 k3d-deploy      # secret postgres-auth + postgres + training + api
kubectl get pods,hpa
.\tasks.ps1 port-forward    # en otra terminal: http://localhost:8081/docs
```

Prueba rápida:

```powershell
Invoke-RestMethod http://localhost:8081/healthz
$id = (Invoke-RestMethod "http://localhost:8081/wells/sample?n=1").idpozo[0]
Invoke-RestMethod -Method Post http://localhost:8081/predict -ContentType "application/json" -Body "{`"idpozo`": $id}"
Invoke-RestMethod http://localhost:8081/stats
```

Entrenamiento como Job dentro del cluster, para registrar el run en PostgreSQL:

```powershell
.\tasks.ps1 k3d-train -Model v2
```

Prueba de carga local corta (k6 dentro del cluster):

```powershell
.\tasks.ps1 loadtest -Vus 10 -Duration 1m -Label k3d-vus10
```

Al terminar: `.\tasks.ps1 k3d-down`.

## Fase 3. GCP: preparación (una sola vez)

1. Abrí `scripts/bootstrap-gcp.ps1`, revisá las variables del bloque 0 (proyecto, pool) y corré los bloques uno por uno. Crea un **provider OIDC nuevo** solo para este repo y agrega roles a `ci-deployer`.
2. En GitHub, en **Settings → Secrets and variables → Actions → Variables**, cargá los valores que imprime el bloque 6:
   `GCP_PROJECT_ID`, `GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_SERVICE_ACCOUNT`, `GCP_DEPLOY_SERVICE_ACCOUNT` y `GCP_REGION`.
3. Hacé un push (o re-ejecutá `ci`). El job `publish` sube `api` y `training` a Artifact Registry con tag `<sha>`.

> En el **primer** deploy el repositorio `tf-vaca-muerta` de Artifact Registry todavía no existe, porque lo crea Terraform. Orden:
> 1. `deploy` con `terraform=true` y `charts` vacío.
> 2. Re-ejecutar `ci` (publica las imágenes).
> 3. `deploy` de nuevo con los charts.

## Fase 4. GKE

1. **Infraestructura:** Actions → `deploy` → Run workflow con `image_tag=<sha>`, `dataset=datasets/<snapshot>.csv.gz`, `terraform=true` y `charts` = (vacío). Crea la red, el cluster `tfvm-dev-gke` (tarda unos 10–15 min), Artifact Registry, el bucket `gs://<proyecto>-tfvm-artifacts` y las cuentas `vm-api` / `vm-trainer`.
2. **Imágenes:** re-ejecutá `ci` sobre `main` y anotá el sha publicado.
3. **Dataset al bucket:**

   ```powershell
   gcloud auth application-default login     # solo si no lo hiciste
   .\tasks.ps1 upload-dataset -Project <proyecto>
   ```

4. **Charts y observabilidad:** `deploy` con `terraform=false`, `charts=postgres training api` y `observability=true`.
5. **Credenciales y entrenamiento en GKE.** El primer entrenamiento promueve cada versión, porque no hay vigente.

   ```powershell
   .\tasks.ps1 gke-creds -Project <proyecto>
   .\tasks.ps1 gke-train -Model v1
   .\tasks.ps1 gke-train -Model v2
   .\tasks.ps1 gke-train -Model v2_notef
   kubectl get pods        # la API pasa a Ready cuando carga v2_notef (recarga cada 120 s)
   ```

6. **Grafana:**

   ```powershell
   kubectl -n observability port-forward svc/kube-prom-grafana 3000:80
   $p = kubectl -n observability get secret grafana-admin -o jsonpath="{.data.admin-password}"
   [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p))
   ```

   Abrí http://localhost:3000 (usuario `admin`) → dashboard **TF Vaca Muerta - API de inferencia** → Alerting.

## Fase 5. Pruebas de carga (GKE)

Ver `docs/resultados.md` (matriz completa y cómo registrar cada corrida).

## Apagar (costos)

```powershell
kubectl delete pvc --all                    # discos de PostgreSQL
```

Después, destruir la infraestructura localmente:

```powershell
cd terraform/envs/dev
terraform init -backend-config="bucket=<proyecto>-tfstate"
terraform destroy -var="project_id=<proyecto>"
```

> El cluster del **laboratorio 8** (`infra-cloud-dev-gke`) es independiente: si sigue encendido también cobra. Apagalo cuando el lab esté entregado.

## Problemas frecuentes

| Síntoma | Causa | Solución |
|---|---|---|
| Pod `api` en 0/1 Ready | No hay `current.json` en el store | Entrenar (`gke-train`) y esperar la recarga, o `POST /admin/reload` |
| `/models` muestra `403` en `errors` | Workload Identity mal ligado | Verificar la anotación del KSA `vm-api` y el binding `workloadIdentityUser` (Terraform) |
| `ImagePullBackOff` | El tag no está en Artifact Registry | Usar el sha que publicó `ci` |
| `ErrImageNeverPull` en k3d | Falta importar la imagen | `.\tasks.ps1 k3d-images` |
| Job de training en `Error` con "sha256 no coincide" | Snapshot modificado | Regenerar con `data-build` y volver a subirlo |
| HPA `<unknown>` | metrics-server todavía sin datos | Esperar 1–2 min |
| `deploy` → `Permission denied` en bucket o IAM | Faltan roles de `ci-deployer` | Bloque 4 de `bootstrap-gcp.ps1` |
