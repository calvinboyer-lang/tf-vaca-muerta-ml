# Bucket de artefactos (datasets y modelos) y las dos cuentas de servicio que lo
# usan desde GKE con Workload Identity. Mínimo privilegio:
#   vm-trainer -> roles/storage.objectAdmin  (escribe versiones y current.json)
#   vm-api     -> roles/storage.objectViewer (solo lee: no puede reemplazar un
#                 model.joblib, que ejecuta código al cargarse)

resource "google_storage_bucket" "artifacts" {
  name                        = var.bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.force_destroy

  # Las versiones de objetos protegen contra sobrescrituras accidentales.
  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 5
    }
    action {
      type = "Delete"
    }
  }

  labels = {
    managed_by = "terraform"
    proyecto   = "tf-vaca-muerta"
  }
}

resource "google_service_account" "trainer" {
  account_id   = "vm-trainer"
  display_name = "TF Vaca Muerta - entrenamiento (escribe en el bucket)"
}

resource "google_service_account" "api" {
  account_id   = "vm-api"
  display_name = "TF Vaca Muerta - API (lee del bucket)"
}

resource "google_storage_bucket_iam_member" "trainer_rw" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.trainer.email}"
}

resource "google_storage_bucket_iam_member" "api_ro" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.api.email}"
}

# Workload Identity: el ServiceAccount de Kubernetes <ns>/<ksa> puede actuar
# como la cuenta de GCP. Solo ese KSA, en ese namespace.
resource "google_service_account_iam_member" "trainer_wi" {
  service_account_id = google_service_account.trainer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.workload_pool}[${var.k8s_namespace}/vm-trainer]"
}

resource "google_service_account_iam_member" "api_wi" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.workload_pool}[${var.k8s_namespace}/vm-api]"
}
