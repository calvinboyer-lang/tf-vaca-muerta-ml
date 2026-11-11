variable "name" {
  description = "Nombre del cluster."
  type        = string
}

variable "project_id" {
  description = "Proyecto de GCP (para el workload pool de Workload Identity)."
  type        = string
}

variable "zone" {
  description = "Zona del cluster zonal."
  type        = string
}

variable "environment" {
  type = string
}

variable "network_id" {
  type = string
}

variable "subnetwork_id" {
  type = string
}

variable "machine_type" {
  description = "Tipo de máquina de los nodos."
  type        = string
  default     = "e2-standard-2"
}

variable "node_count" {
  description = "Nodos iniciales del pool."
  type        = number
  default     = 2
}

variable "min_node_count" {
  type    = number
  default = 1
}

variable "max_node_count" {
  description = "Tope del autoescalado de nodos (acota el costo de las pruebas de HPA)."
  type        = number
  default     = 4
}
