resource "google_storage_bucket" "photos" {
  project                     = var.project_id
  name                        = var.photo_bucket_name
  location                    = var.storage_location
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  cors {
    origin          = ["*"]
    method          = ["GET", "PUT", "POST", "HEAD"]
    response_header = ["*"]
    max_age_seconds = 3600
  }

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_firebase_storage_bucket" "photos" {
  provider  = google-beta
  project   = var.project_id
  bucket_id = google_storage_bucket.photos.name

  depends_on = [google_firebase_project.this]
}

resource "google_firebaserules_ruleset" "storage" {
  project = var.project_id

  source {
    files {
      name    = "storage.rules"
      content = file("${path.module}/rules/storage.rules")
    }
  }

  depends_on = [google_firebase_storage_bucket.photos]
}

resource "google_firebaserules_release" "storage" {
  project      = var.project_id
  name         = "firebase.storage/${google_storage_bucket.photos.name}"
  ruleset_name = "projects/${var.project_id}/rulesets/${google_firebaserules_ruleset.storage.name}"

  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.storage]
  }
}
