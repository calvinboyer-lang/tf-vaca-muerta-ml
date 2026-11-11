# Entorno dev del TF: red + GKE (Workload Identity) + Artifact Registry + bucket.
# Estado remoto en gs://<proyecto>-tfstate con prefijo propio (backend.tf), así
# no se mezcla con el del laboratorio 8 aunque use el mismo proyecto y bucket.

terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name_prefix = "tfvm-dev"
}

module "network" {
  source = "../../modules/network"

  name_prefix = local.name_prefix
  region      = var.region
  subnet_cidr = "10.20.0.0/24"
}

module "artifact_registry" {
  source = "../../modules/artifact-registry"

  project_id    = var.project_id
  region        = var.region
  repository_id = var.ar_repository
}

module "gke" {
  source = "../../modules/gke"
  count  = var.enable_gke ? 1 : 0

  name           = "${local.name_prefix}-gke"
  project_id     = var.project_id
  zone           = var.zone
  environment    = "dev"
  network_id     = module.network.network_id
  subnetwork_id  = module.network.subnetwork_id
  machine_type   = var.machine_type
  node_count     = var.node_count
  min_node_count = var.min_node_count
  max_node_count = var.max_node_count
}

module "storage" {
  source = "../../modules/storage"

  bucket_name   = "${var.project_id}-tfvm-artifacts"
  region        = var.region
  workload_pool = "${var.project_id}.svc.id.goog"
}
