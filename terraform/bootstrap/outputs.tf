output "project_id" {
  value = var.project_id
}

output "firebase_web_app_id" {
  value = google_firebase_web_app.web.app_id
}

output "firebase_web_config" {
  description = "Firebase JS SDK config for the web app. Paste into the frontend."
  value = {
    apiKey            = data.google_firebase_web_app_config.web.api_key
    authDomain        = data.google_firebase_web_app_config.web.auth_domain
    projectId         = var.project_id
    storageBucket     = google_storage_bucket.photos.name
    messagingSenderId = data.google_firebase_web_app_config.web.messaging_sender_id
    appId             = google_firebase_web_app.web.app_id
    measurementId     = data.google_firebase_web_app_config.web.measurement_id
  }
  sensitive = true
}

output "photo_bucket" {
  value = google_storage_bucket.photos.name
}

output "published_bucket" {
  description = "Public bucket the reviewer writes approved trees and photos to; read by data/raw/community_tree_info.py."
  value       = google_storage_bucket.published.name
}
