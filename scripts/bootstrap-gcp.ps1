<#
.SYNOPSIS
  Preparación de GCP para el TF (se corre UNA vez, en PowerShell, con gcloud logueado).

  Reutiliza lo del laboratorio 8 (proyecto, bucket de tfstate, pool de Workload
  Identity "github-pool", cuenta ci-deployer) y agrega lo que el TF necesita:

  1. APIs habilitadas.
  2. Un PROVIDER OIDC nuevo en el mismo pool, restringido a este repo. El provider
     del lab tiene attribute-condition para el repo del lab: no sirve para este,
     y no se modifica para no romper el lab.
  3. Roles extra para ci-deployer: Terraform ahora crea un bucket y cuentas de
     servicio (storage.admin, iam.serviceAccountAdmin).
  4. Permiso para que GitHub Actions de ESTE repo actúe como ci-deployer.

  Correr bloque por bloque (copiar y pegar), revisando la salida de cada uno.
#>

# ---- 0. Variables -----------------------------------------------------------
$PROJECT_ID = "unlu-ia-boyer-lab-511417"          # tu proyecto (el del lab 8)
$REGION     = "us-central1"
$POOL_ID    = "github-pool"                       # pool creado en la clase 4
$PROVIDER   = "github-provider-tfvm"              # provider NUEVO para este repo
$REPO       = "calvinboyer-lang/tf-vaca-muerta-ml"
$DSA        = "ci-deployer@$PROJECT_ID.iam.gserviceaccount.com"

gcloud config set project $PROJECT_ID
$PROJECT_NUMBER = gcloud projects describe $PROJECT_ID --format="value(projectNumber)"
"Proyecto $PROJECT_ID ($PROJECT_NUMBER)"

# ---- 1. APIs ----------------------------------------------------------------
gcloud services enable container.googleapis.com compute.googleapis.com `
  artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com `
  storage.googleapis.com sts.googleapis.com

# ---- 2. Verificar lo que viene del lab -------------------------------------
gcloud storage buckets describe "gs://$PROJECT_ID-tfstate" --format="value(name)"
gcloud iam workload-identity-pools describe $POOL_ID --location=global --format="value(name)"
gcloud iam service-accounts describe $DSA --format="value(email)"
# Si alguno falla: ver docs/despliegue.md, sección "Si no tenés lo del lab".

# ---- 3. Provider OIDC para este repo ---------------------------------------
gcloud iam workload-identity-pools providers create-oidc $PROVIDER `
  --location=global --workload-identity-pool=$POOL_ID `
  --issuer-uri="https://token.actions.githubusercontent.com" `
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" `
  --attribute-condition="assertion.repository=='$REPO'"

# ---- 4. Roles de ci-deployer -----------------------------------------------
foreach ($role in @("roles/compute.admin", "roles/container.admin", "roles/artifactregistry.admin",
                    "roles/iam.serviceAccountUser", "roles/storage.admin", "roles/iam.serviceAccountAdmin")) {
  gcloud projects add-iam-policy-binding $PROJECT_ID --member="serviceAccount:$DSA" --role=$role --condition=None | Out-Null
  "rol $role -> ci-deployer"
}

# ---- 5. GitHub Actions de este repo puede actuar como ci-deployer ----------
gcloud iam service-accounts add-iam-policy-binding $DSA --role=roles/iam.workloadIdentityUser `
  --member="principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL_ID/attribute.repository/$REPO"

# ---- 6. Valores para cargar en GitHub (Settings > Secrets and variables > Actions > Variables)
""
"GCP_PROJECT_ID                 = $PROJECT_ID"
"GCP_WORKLOAD_IDENTITY_PROVIDER = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL_ID/providers/$PROVIDER"
"GCP_SERVICE_ACCOUNT            = $DSA"
"GCP_DEPLOY_SERVICE_ACCOUNT     = $DSA"
"GCP_REGION                     = $REGION"
