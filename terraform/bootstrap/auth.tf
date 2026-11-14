resource "google_identity_platform_config" "default" {
  provider = google-beta
  project  = var.project_id

  # Every host the web app runs on. Authoritative: a domain missing here is
  # removed from Firebase on apply. The app is served from GitHub Pages
  # (/tree_reporting/) and arborary.world (/explore/).
  authorized_domains = [
    "sf-tree-reporting-prod.firebaseapp.com",
    "sf-tree-reporting-prod.web.app",
    "localhost",
    "greenmtnboy.github.io",
    "arborary.world",
  ]

  sign_in {
    allow_duplicate_emails = false

    anonymous {
      enabled = true
    }

    email {
      enabled           = false
      password_required = false
    }
  }

  depends_on = [google_firebase_project.this]
}
