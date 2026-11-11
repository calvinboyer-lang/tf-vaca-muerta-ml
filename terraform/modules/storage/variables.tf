variable "bucket_name" {
  type = string
}

variable "region" {
  type = string
}

variable "workload_pool" {
  description = "<proyecto>.svc.id.goog (salida del módulo gke)."
  type        = string
}

variable "k8s_namespace" {
  type    = string
  default = "default"
}

variable "force_destroy" {
  description = "Permite borrar el bucket con objetos adentro al destruir (entorno académico)."
  type        = bool
  default     = true
}
