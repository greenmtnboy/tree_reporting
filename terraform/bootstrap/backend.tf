terraform {
  required_version = ">= 1.5.0"

  backend "gcs" {
    bucket = "sf-tree-reporting-tfstate"
    prefix = "bootstrap"
  }
}
