# Firebase bootstrap (Terraform)

Manages the Firebase stack for `sf-tree-reporting-prod`: Firestore, Storage, Identity Platform (anonymous auth), and a web-app registration used by the frontend.

## Prerequisites

These were done manually before the first apply:

- Project `sf-tree-reporting-prod` created, billing linked
- `gs://sf-tree-reporting-tfstate` bucket created with versioning
- APIs enabled via `gcloud services enable ...` (Terraform re-declares them for idempotency)
- ADC quota project set: `gcloud auth application-default set-quota-project sf-tree-reporting-prod`

## Usage

```bash
cd terraform/bootstrap
terraform init
terraform plan
terraform apply
```

The Firebase web config is in the `firebase_web_config` output (marked sensitive). Read it with:

```bash
terraform output -json firebase_web_config
```

Paste that into the frontend's Firebase initializer (e.g. `src/src/lib/firebase.ts`).

## One-way doors

- **Firestore location** (`us-central1`): cannot be changed after the database is created without a migration.
- **Storage bucket location** (`us-central1`): cannot be changed after the bucket is created.
- **Firestore delete protection** is enabled; disable it explicitly in `firestore.tf` if you need to destroy the database.

## Security rules

Rules live in `rules/firestore.rules` and `rules/storage.rules`. Terraform creates a new ruleset and release whenever the file content changes.

Current posture: authenticated users can only create submissions/checkins owned by their own UID, and read/write only their own data. Update/delete are disallowed for submissions (offline pipeline handles moderation via admin SDK).
