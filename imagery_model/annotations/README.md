# Registration annotations

This directory contains compact, human-reviewed source data and is intentionally tracked in Git.
The QA server and `urban-tree-ml qa snapshot` publish bundles at:

```text
annotations/<city>/<review-id>/
```

Each bundle pairs `reviews.json` with the exact `manifest.json` that defines its stable sample and
scene IDs. `bundle.json` records provenance, counts, and SHA-256 checksums. A current finalized
bundle also contains `training-feedback.json`; any later review edit invalidates that derived file
until finalization runs again.

NAIP rasters, rendered review PNGs, chips, and checkpoints do not belong here. They remain under
the Git-ignored artifacts tree and can be rebuilt from the manifest and checked-in model code.
