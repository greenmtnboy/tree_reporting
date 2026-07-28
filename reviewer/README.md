# Local submission reviewer

This localhost-only service lists pending Firestore submissions and uses the
Firebase Admin SDK to approve or reject them. Approval atomically creates a
canonical record in `publishedTrees` and marks the submission `published`.

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
`GOOGLE_CLOUD_PROJECT`, `FIREBASE_STORAGE_BUCKET`, or `REVIEWER_PORT` to
override the defaults.

The reviewer requires a Firestore composite index on `status` ascending and
`submittedAt` ascending. Terraform manages that index.

## Data refresh

Approval does not trigger a rebuild. The normal scheduled `trilogy refresh raw`
run reads the rules-public `publishedTrees` collection through
`data/raw/community_tree_info.py`. Every city model imports that shared partial
source, filters it through its existing `complete where city = ...` boundary,
and writes the combined municipal + community rows to the normal versioned
city Parquet.

`community_update_time.py` uses the newest `publishedAt` value as a freshness
input. A new approval therefore makes the affected materializations stale on
the next scheduled run. Trees without an identified species are emitted as
`Unknown` because `species` is a non-null key in the canonical model.
