# GKE zonal (un solo plano de control: más barato) con un node pool propio.
# Cambios respecto del template del laboratorio:
#   - Workload Identity: los pods acceden a GCS como cuentas de servicio de GCP
#     sin llaves JSON (workload_pool + GKE_METADATA en los nodos).
#   - Nodos e2-standard-2 (2 vCPU, 8 GB) con autoescalado: la API con HPA de
#     2 a 10 réplicas, PostgreSQL y el stack de observabilidad no entran en
#     e2-medium (4 GB), donde Grafana se reiniciaba por memoria.

resource "google_container_cluster" "this" {
  name     = var.name
  location = var.zone

  remove_default_node_pool = true
  initial_node_count       = 1

  network    = var.network_id
  subnetwork = var.subnetwork_id

  deletion_protection = false

  release_channel {
    channel = "REGULAR"
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }
}

resource "google_container_node_pool" "primary" {
  name     = "${var.name}-pool"
  cluster  = google_container_cluster.this.name
  location = var.zone

  node_count = var.node_count

  node_config {
    machine_type = var.machine_type
    disk_size_gb = 40
    disk_type    = "pd-standard"

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]

    # Necesario para Workload Identity: los pods ven el metadata server de GKE.
    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    labels = {
      environment = var.environment
      managed_by  = "terraform"
      proyecto    = "tf-vaca-muerta"
    }
  }

  autoscaling {
    min_node_count = var.min_node_count
    max_node_count = var.max_node_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
