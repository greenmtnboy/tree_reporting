terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.10"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.10"
    }
  }
}

provider "google" {
  project               = var.project_id
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  user_project_override = true
}
