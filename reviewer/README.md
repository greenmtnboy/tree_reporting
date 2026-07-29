# Local submission reviewer

This localhost-only service lists pending Firestore submissions and uses the
Firebase Admin SDK to approve or reject them.

## Run

Authenticate Application Default Credentials once:

```powershell
gcloud auth application-default login
gcloud auth application-default set-quota-project sf-tree-reporting-prod
```

Then:

```powershell
cd reviewer
pnpm install
pnpm dev
```

Open <http://127.0.0.1:4174>. The server binds only to localhost. Set
`GOOGLE_CLOUD_PROJECT`, `FIREBASE_STORAGE_BUCKET`, `PUBLISHED_BUCKET`, or
`REVIEWER_PORT` to override the defaults.

The reviewer requires a Firestore composite index on `status` ascending and
`submittedAt` ascending, and write access to the public
`sf-tree-reporting-published` bucket. Terraform manages both.

## What approval does

Approving is the single gate between a private upload and public data. It:

1. **Re-encodes each photo** through `sharp` and writes it to the public
   `sf-tree-reporting-published` bucket under `community/photos/`. The client
   already strips EXIF via a canvas re-encode, but the storage rules only check
   `contentType`, so anything speaking the Storage API can upload a JPEG with
   intact GPS tags. Re-encoding here means a published photo carries no EXIF,
   IPTC, or XMP regardless of how it was uploaded.
2. **Creates the `publishedTrees` record** and marks the submission `published`,
   in one transaction.
3. **Rewrites the public export** — `community/published_trees.ndjson` (the rows
   the data pipeline reads) and `community/manifest.json` (the freshness
   timestamp).

Rejected and pending submissions never leave the private `submissions` bucket,
which has `public_access_prevention = "enforced"`.

Submissions whose `city` is not one of the supported city codes are rejected at
approval rather than silently dropped later by the ingest.

`POST /api/republish` rebuilds the export from Firestore without approving
anything — use it for the first run, or if an approval committed but the export
write failed.

## Data refresh

Approval does not trigger a rebuild. The normal scheduled `trilogy refresh raw`
run reads the public export through `data/raw/community_tree_info.py`. Each city
model declares a `complete where city = 'X' and {code}_source = 'COMMUNITY_X'`
partition over that shared ingest, which Trilogy unions with the municipal
source(s) into the city's usual versioned Parquet.

`community_update_time.py` reads `manifest.json` as a freshness input, so a new
approval makes the affected materialization stale on the next scheduled run.
The manifest carries `latestPublishedAtByCity`, and each city's model probes
only its own entry, so approving a tree in Boston rebuilds Boston's Parquet
alone rather than all fourteen.

Trees without an identified species are emitted as `Unknown` because `species`
is a non-null key in the canonical model.

The pipeline reads a **public GCS object rather than Firestore** on purpose:
`firestore.googleapis.com` enforces IAM, not security rules, so a public
`allow read` rule still returns 403 to the unauthenticated pipeline. See
`EXTENDING.md` for the full rationale.
