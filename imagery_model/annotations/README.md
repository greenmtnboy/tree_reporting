# Registration annotations

Canonical bundles are versioned in
[`arborary-world/training-data`](https://github.com/arborary-world/training-data), not in this code
repository. Set `TREE_ML_ANNOTATIONS_ROOT` to that checkout's `annotations` directory. This local
directory is only the Git-ignored fallback when the variable is unset.

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
