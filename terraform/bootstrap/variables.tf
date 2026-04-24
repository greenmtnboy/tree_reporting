variable "project_id" {
  description = "GCP project ID hosting the Firebase stack."
  type        = string
  default     = "sf-tree-reporting-prod"
}

variable "firestore_location" {
  description = "Firestore database location. One-way door — cannot be changed after creation."
  type        = string
  default     = "us-central1"
}

variable "storage_location" {
  description = "Default Firebase Storage bucket location. One-way door per bucket."
  type        = string
  default     = "us-central1"
}

variable "photo_bucket_name" {
  description = "GCS bucket for user-submitted photos. Must be globally unique."
  type        = string
  default     = "sf-tree-reporting-submissions"
}
