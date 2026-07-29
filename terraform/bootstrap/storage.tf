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

# Approved submissions only. The `photos` bucket above enforces public access
# prevention and holds every upload including rejected ones; nothing there is
# ever world-readable. A reviewer approval is the single gate that copies a
# re-encoded photo into this bucket, so "published" is an explicit human action
# rather than a property of having uploaded.
resource "google_storage_bucket" "published" {
  project                     = var.project_id
  name                        = var.published_bucket_name
  location                    = var.storage_location
  uniform_bucket_level_access = true
  public_access_prevention    = "inherited"

  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD"]
    response_header = ["*"]
    max_age_seconds = 3600
  }

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "published_public_read" {
  bucket = google_storage_bucket.published.name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
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
