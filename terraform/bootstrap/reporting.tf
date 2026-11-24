# Read access for the daily arborary.world report. trilogy-cloud's
# platform-reporting org runs it (platform_models/platform_reporting/
# arborary_report.preql in that repo) as its tenant service account, which
# reads accounts from Firebase Auth and contributions from Firestore and
# copies them into BigQuery in trilogy-data-prod.
#
# - firebaseauth.viewer: list accounts (accounts:batchGet).
# - datastore.viewer: read Firestore documents. It covers every collection;
#   the report asks only for the fields it needs.
# - serviceUsageConsumer: the reads are billed to this project
#   (x-goog-user-project), because the caller's own project does not enable
#   these APIs.

variable "reporting_readers" {
  description = "Service accounts that read accounts and contributions for reporting."
  type        = list(string)
  default     = ["t-ebe013c5601e-prod@trilogy-data-prod.iam.gserviceaccount.com"]
}

locals {
  reporting_roles = [
    "roles/firebaseauth.viewer",
    "roles/datastore.viewer",
    "roles/serviceusage.serviceUsageConsumer",
  ]
  reporting_grants = {
    for pair in setproduct(var.reporting_readers, local.reporting_roles) :
    "${pair[0]}/${pair[1]}" => { member = pair[0], role = pair[1] }
  }
}

resource "google_project_iam_member" "reporting" {
  for_each = local.reporting_grants

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${each.value.member}"
}
