output "bucket" {
  value = google_storage_bucket.artifacts.name
}

output "store_uri" {
  value = "gs://${google_storage_bucket.artifacts.name}/vm"
}

output "trainer_sa" {
  value = google_service_account.trainer.email
}

output "api_sa" {
  value = google_service_account.api.email
}
