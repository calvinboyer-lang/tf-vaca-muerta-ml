terraform {
  backend "gcs" {
    # bucket = "<proyecto>-tfstate"   # -backend-config o backend.hcl (no versionado)
    prefix = "tf-vaca-muerta/dev"
  }
}
