# Training and curation backup — September 21, 2026

## Code integration

Destination: `greenmtnboy/tree_reporting`, branch
`codex/training-curation-backup-20260921`, based on remote main `f69e5f3`.
Code commit: `88e9f1aaf526160d8fc91b3af71ef6b9467b10fe`. A later collector
update made concurrently in the source worktree is included in a follow-up:
SSH keepalives and bounded status polling, without acknowledging failed collection.

The source is the active reviewer's `model_training` worktree at `429f04e`,
including its uncommitted imagery files and the final imagery state from all
30 earlier training commits absent from main. Integration was performed in a
separate worktree. Main's newer tile exporter and its tests were preserved.

Included: interchangeable ResNet34/ConvNeXt-Tiny/Swin-Tiny backbones, crown
heads and center targets, warm-start recipes, taxonomy preparation, acquisition
date exclusions, immutable run inputs, masked and crown-aware evaluation,
full training inference coverage, canonical curation storage and snapshots,
multi-city review, manual-tree movement and undo, corrected Street View,
prediction crowns, localized dimming, and X save/confirm/deselect behavior.

Operational scripts and historical reports are retained. See
[`imagery_model/lambda/README.md`](../imagery_model/lambda/README.md) for the
reviewed distinction between reusable tools and one-off provenance, including
`recover_swin_v5.py`. Manual fixtures and diagnostics retain their original
local paths; they were not executed against the live reviewer.

No unrelated tracked deletions were staged. The four missing imagery documents
(`DESIGN.md`, `RUN_PLAN.md`, `JOINT_RUN.md`, `annotations/README.md`) were
preserved from `model_training` HEAD in the integration worktree because their
deletion intent is unknown. The source worktree was not restored or changed.
The original handoff remains untracked in the main application checkout.

Raw imagery, chips, previews, model checkpoints, run archives, credentials,
live annotation directories, and atomic temporary files are excluded from the
code commits. Shell scripts have LF line endings and executable Git modes.

## Validation

The isolated CPU environment passed **253 tests**, with **one skipped module**
(`test_losses.py`, missing PyTorch). Three additional PyTorch-dependent modules
could not collect and were explicitly excluded from the final CPU run:
`test_dataset.py`, `test_evaluation.py`, and `test_model.py`. No GPU stack was
installed, no training was launched, and no GPU execution is claimed.

Eight initial failures were stale fixtures for renamed review controls,
localized dimming, acquisition-date requirements, or newly required JavaScript
state. Those fixtures were updated only in the integration branch. Existing
behavioral coverage includes draft recovery, scoped saves, snapshots, masks,
goal metrics, frozen input integrity, inference coverage, warm recipes, crown
targets, navigation, manual movement/undo, and confirmation interactions.

Reproduce from `imagery_model/`, with `PYTHONPATH` set to its `src` directory:

```powershell
uv run --offline --no-project --isolated --with pytest --with numpy --with pandas --with pyarrow --with pydantic --with pyyaml --with pyproj==3.7.2 --with rasterio --with pillow --with duckdb --with typer python -m pytest tests -q -o addopts='' --ignore=tests/test_dataset.py --ignore=tests/test_evaluation.py --ignore=tests/test_model.py
```

Python syntax and internal module imports were checked; staged bytes were
scanned for credential patterns and unexpected binary/large files. Git's staged
whitespace check passed. No frontend or data-pipeline code changed.

## Annotation snapshot

The existing reviewer backup endpoint captured immutable SF and Boston bundles
under its snapshot lock and pushed through its isolated publisher directly to
`arborary-world/training-data` **main**:
`8cb778ba38eedf5d4391ecc7c40fe05cc015453b` (remote SHA verified).

| City | Captured revision | Feedback current | Tree records | Added trees | Masks |
| --- | --- | --- | ---: | ---: | ---: |
| SF | `b0bb410d80f1b9b99ca8e0e837c7093c13aad5977907afbd78d5434c161bdb1b` | Yes, already published before this backup | 40,577 | 1,538 | 3 |
| Boston | `e8b13835b22df6bfd4ba244de5ecfea8d4246ec75196078374d88c36f9451b08` | No; draft preserved | 26,936 | 3,000 | 29 |

Validation checked bundle file byte lengths and SHA256, commit bytes versus the
immutable ZIPs, city/review identity, schema, canonical expanded state hashes,
tree and mask coordinates, manifest and acquisition identities, and feedback
currentness. Boston's superseded feedback was not restored or finalized.
The archive recovery tool in the annotation repository agrees with the code
implementation after Git newline normalization.

A stale backup lock from September 17 referred to a process that no longer
existed. It was preserved in the local audit folder before invoking the normal
backup endpoint. The live reviewer remained running and curation continued.
Edits made after capture naturally belong to newer revisions; this snapshot
does not claim to include subsequent edits.

The private archive target is `gs://arborary-world-curation-archive`. The
whole-history backup includes roughly 29,000 revision ZIPs (57 GB locally);
both captured revision ZIPs were uploaded and their GCS object sizes and MD5
hashes verified against local immutable bytes. The existing backup was still
syncing historical revisions when this report was prepared. Do not infer
whole-history upload completion from the Git push or the two ZIP checks alone.

## Latest model artifacts

The v5 report and small goal metrics/provenance JSON records are versioned in
`imagery_model/reports/`. The local selected checkpoint `016.ckpt` was rehashed
and matches `9c656fecce19f33cc5885667ce30f7f3695acd5833cea967fa4fe77c72593a22`.
The recovered result archive, source archive, and frozen-input archive hashes
are in `swin-v5-artifact-provenance.json`; the archives themselves remain outside
Git. The earlier recovery verified the result archive from the persistent
Lambda filesystem. That filesystem was not mounted or rechecked during this
backup, and no checkpoint upload to the annotation bucket is claimed.
