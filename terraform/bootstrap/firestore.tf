resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.firestore_location
  type        = "FIRESTORE_NATIVE"

  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  delete_protection_state = "DELETE_PROTECTION_ENABLED"

  depends_on = [google_firebase_project.this]
}

resource "google_firebaserules_ruleset" "firestore" {
  project = var.project_id

  source {
    files {
      name    = "firestore.rules"
      content = file("${path.module}/rules/firestore.rules")
    }
  }

  depends_on = [google_firestore_database.default]
}

resource "google_firebaserules_release" "firestore" {
  project      = var.project_id
  name         = "cloud.firestore"
  ruleset_name = "projects/${var.project_id}/rulesets/${google_firebaserules_ruleset.firestore.name}"

  lifecycle {
    replace_triggered_by = [google_firebaserules_ruleset.firestore]
  }
}

resource "google_firestore_index" "submissions_by_user_recent" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "submissions"

  fields {
    field_path = "userId"
    order      = "ASCENDING"
  }

  fields {
    field_path = "submittedAt"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}

resource "google_firestore_index" "checkins_by_user_recent" {
  project    = var.project_id
  database   = google_firestore_database.default.name
  collection = "checkins"

  fields {
    field_path = "userId"
    order      = "ASCENDING"
  }

  fields {
    field_path = "at"
    order      = "DESCENDING"
  }

  fields {
    field_path = "__name__"
    order      = "DESCENDING"
  }
}
