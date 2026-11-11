output "artifact_registry_url" {
  value = module.artifact_registry.repository_url
}

output "store_uri" {
  value = module.storage.store_uri
}

output "api_service_account" {
  value = module.storage.api_sa
}

output "trainer_service_account" {
  value = module.storage.trainer_sa
}

output "gke_get_credentials" {
  value = var.enable_gke ? "gcloud container clusters get-credentials ${module.gke[0].cluster_name} --zone ${var.zone} --project ${var.project_id}" : null
}
