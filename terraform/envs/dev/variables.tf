variable "project_id" {
  description = "Proyecto de GCP. Sin default: nunca se escribe en el repo."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "zone" {
  type    = string
  default = "us-central1-a"
}

variable "ar_repository" {
  description = "Repositorio de Artifact Registry de las imágenes api y training."
  type        = string
  default     = "tf-vaca-muerta"
}

variable "enable_gke" {
  description = "Crea el cluster. Se cobra por hora: apagarlo (false + apply) al terminar las pruebas."
  type        = bool
  default     = true
}

variable "machine_type" {
  type    = string
  default = "e2-standard-2"
}

variable "node_count" {
  type    = number
  default = 2
}

variable "min_node_count" {
  type    = number
  default = 1
}

variable "max_node_count" {
  type    = number
  default = 4
}
