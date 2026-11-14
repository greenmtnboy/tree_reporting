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

## Google sign-in

The frontend supports Google account sign-in and anonymous-account upgrade, but it is currently enabled manually in Firebase Console rather than managed by Terraform.

1. Open Firebase Console for `sf-tree-reporting-prod`.
2. Go to `Authentication` -> `Sign-in method` -> `Google`.
3. Enable the provider, choose the public-facing app name, set a support email, and save.
4. Go to `Authentication` -> `Settings` -> `Authorized domains`.
5. Add every runtime host used by the web app.

Notes:

- Projects created after April 28, 2025 no longer include `localhost` as an authorized domain by default, so add it manually for local development if needed.
- Enter hostnames only in Authorized domains, for example `localhost` or `greenmtnboy.github.io`, not full URLs, ports, or paths.
- Authorized domains are managed by Terraform (`authorized_domains` in `auth.tf`), and the list is authoritative: a domain added only in the console is removed on the next apply. The app is served from `greenmtnboy.github.io` (`/tree_reporting/`) and `arborary.world` (`/explore/`).
- The Street View key (`VITE_GOOGLE_MAPS_API_KEY`, console name `embed-sf-tree-reporting`) is restricted to the Maps Embed API, not by referrer, so a new host needs no key change. If a referrer allow list is ever added, it must name both hosts.
- Use a 64-bit (windows_amd64) Terraform. The 32-bit build's Google provider crashes reading the 12-digit project number during a plan.
- The app links a signed-in anonymous user to Google when possible so existing submissions keep the same Firebase `uid`.

## One-way doors

- **Firestore location** (`us-central1`): cannot be changed after the database is created without a migration.
- **Storage bucket location** (`us-central1`): cannot be changed after the bucket is created.
- **Firestore delete protection** is enabled; disable it explicitly in `firestore.tf` if you need to destroy the database.

## Security rules

Rules live in `rules/firestore.rules` and `rules/storage.rules`. Terraform creates a new ruleset and release whenever the file content changes.

Current posture: authenticated users can only create submissions/checkins owned by their own UID, and read/write only their own data. Update/delete are disallowed for submissions (offline pipeline handles moderation via admin SDK).

`rules/tests/` runs both files against the Firebase emulators (the `firestore-rules` CI job); see its README. Deploy rules before a frontend that depends on them: the check-in batch includes the counter and marker writes, so an app ahead of its rules fails every check-in, not just the count.
