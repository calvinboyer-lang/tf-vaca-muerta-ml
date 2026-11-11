output "cluster_name" {
  value = google_container_cluster.this.name
}

output "endpoint" {
  value     = google_container_cluster.this.endpoint
  sensitive = true
}

output "workload_pool" {
  # Se lee del cluster creado (no se arma como texto) para que los bindings de
  # Workload Identity esperen a que exista el pool PROJECT.svc.id.goog.
  value = google_container_cluster.this.workload_identity_config[0].workload_pool
}
