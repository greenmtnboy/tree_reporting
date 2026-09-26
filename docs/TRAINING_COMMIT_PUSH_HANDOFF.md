# Training / curation commit-and-push handoff

## Mission and boundaries

Prepare reviewable commits and push the accumulated model-training and curation
work to the appropriate repositories. Preserve all annotations and the running
reviewer. Do not launch training, change labels, finalize unpublished feedback,
restart the reviewer, merge PRs, or force-push as part of this task. Ask before
any such expansion. This handoff was written September 21, 2026; recheck state.

The author of this handoff has not committed or pushed anything for this request.

## Repositories and paths (verified locally)

| Purpose | Local path | Remote / current branch |
|---|---|---|
| Main application checkout | `C:/Users/ethan/coding_projects/sf_tree_reporting` | `https://github.com/greenmtnboy/tree_reporting.git`, `cambridge-portal-duplicates`, HEAD `e5837a5` |
| **Actual current training/reviewer source** | `C:/Users/ethan/AppData/Local/Temp/sf-tree-review-done` | Same Git repository, branch `model_training`, HEAD `429f04e` |
| Live annotation mirror | `C:/Users/ethan/coding_projects/training-data` | `https://github.com/arborary-world/training-data.git`, `main`, HEAD `8333bd7` |
| Isolated annotation publisher clone | `C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/curation-archive/publisher` | Same training-data remote; clean at `0ae7ee1c30c26a5adf79c8cb5711520c3b711087` |

Main application checkout was clean before adding this document. Its imagery
source is NOT the source used by the running reviewer: use the temporary
worktree's `imagery_model/`. Do not replace it with the older main-checkout copy.
The current application remote is `greenmtnboy/tree_reporting`, despite older
documentation linking `arborary-world/tree_reporting`. Inspect GitHub canonical
repository/redirect and permissions before pushing; do not silently change origin.

Artifacts are under the main checkout's `imagery_model/artifacts`, not the
temporary worktree. They are data, not another code checkout.

## Critical hazards

1. **Never run repository-wide `git add -A`, `git add .`, or `git commit -a` in
   the temporary worktree.** It reports hundreds of unrelated tracked deletions
   across `src/`, `data/`, workflows, docs, and Terraform. Their cause has not
   been established. They are not part of this training work. Do not stage,
   restore, reset, clean, or propagate them. Stage reviewed paths explicitly.
2. Do not rebase/switch/clean the active reviewer worktree. Integrate its code
   through a separate clean worktree on a `codex/` branch when needed.
3. The live annotation clone is older than the publisher clone and has active
   autosave changes. Never `pull`, checkout, rebase, reset, or stage a moving
   snapshot there. Prefer immutable snapshots and an isolated integration clone.
4. Secrets are in the main `.env` and `.secrets/`. Never print or commit them.
   Also scan source, fixtures, reports and URLs for embedded credentials and
   Google API keys. Do not upload raw imagery, previews, chips or checkpoints
   to Git. Do not force-add ignored artifacts.
5. This is code/data versioning, not permission to freeze more training inputs
   or overwrite current annotations with a run's historical frozen labels.

## Code repository: inventory and package the complete feature set

Read applicable AGENTS.md first. In the training worktree inspect:

```powershell
git status --short -- imagery_model
git diff --stat -- imagery_model
git diff -- imagery_model
git ls-files --others --exclude-standard -- imagery_model
git log --oneline --decorate -15
```

Observed: 42 modified tracked imagery files, plus many new modules/tests/reports.
Untracked files are essential dependencies, not disposable scratch files.
Inspect branch commits versus freshly fetched remote main as well: previous
training commits may not yet be upstream. Copying only the dirty diff could
omit those changes. Conversely, do not transplant the entire old application.

Suggested logical groups (keep dependencies together; don't force artificial splits):

- Model/training: interchangeable ResNet34, ConvNeXt-Tiny and Swin-Tiny backbones,
  shared heads including crown radius, crown-aware center targets/loss, warm-start
  recipes, taxonomy/genus work, imagery acquisition/planting-date exclusions,
  curation-aware dataset preparation and immutable run inputs.
- Evaluation: goal-aligned masked scoring, crown-aware matching, native and
  common-label comparisons, regression/assignment generation, full train inference
  and coverage manifests. Do not relabel inventory-only scores as goal-aligned.
- Curation storage/reviewer: canonical image-scoped tree records and world-coordinate
  masks/crowns, manual added trees/species/radii, snapshot/archive persistence,
  paging/scoped saves, navigation/filter state, multi-city review and model overlays.
- Recent UI interactions: manual-tree WASD and undo, corrected-position Street View
  and pixel close-up, prediction crown circles, localized selection dimming,
  **X = save/confirm/deselect** without changing classification, persistent faint
  predictions. Tests for these interactions must ship with their implementation.
- Operational code: Lambda launch/supervision/collection, configs, Dockerfile,
  dependency lockfile, README and curated reports.

Review `lambda/` scripts individually. `recover_swin_v5.py` is a one-off hardcoded
recovery utility, not a general launcher. Either deliberately retain it as clearly
documented operational provenance, or leave it out of the production commit and
record that decision. Other one-off scripts, manual fixtures and reports with
local paths need the same review. Do not delete them from the active worktree.

Integration approach: create a clean `codex/` worktree from the verified target
base, then port only the intended imagery changes and necessary earlier commits.
Use explicit paths and preserve user changes. Inspect differences against the
destination's existing imagery code rather than blindly overwriting a newer copy.
Bring this handoff over from the main checkout if retaining it in the PR.

## Annotation repository: coherent snapshots, not a live directory dump

Source code: `imagery_model/src/urban_tree_ml/curation_archive.py` and
`tree_curation.py`. Existing backup flow captures a stable revision, commits via
the isolated publisher, verifies committed bytes against bundle hashes, and
uploads immutable acquisition/assets/revision objects to
`gs://arborary-world-curation-archive`.

Inspect first:

- `artifacts/curation-archive/git-status/*.json`, `revisions/`, `uploaded.json`
  and publisher Git history against the current remote.
- Live `training-data/annotations/{ussfo,usbos}/.../{bundle,manifest,reviews}.json`.
- Snapshot worker health at `/api/snapshot-status?city=ussfo` and `city=usbos`.

The publisher was clean at `0ae7ee1`; its per-city receipts referenced that
commit at September 17, 23:40 UTC. That does **not** establish that today's
annotations have already been pushed. Fetch and inspect newer remote history.

Live clone observations: both cities' bundle/manifest/reviews modified;
both `training-feedback.json` files deleted; an untracked Boston
`.manifest.json.<uuid>.tmp`; untracked `tools/curation_archive.py`.
SF bundle reported `feedback_current: false` when inspected. A missing finalized
feedback file can be intentional when new draft edits supersede it. Preserve
drafts; do not restore stale feedback or mark it current to obtain a clean Git status.
Do not commit temporary files or delete one that an active writer may own.

Use the existing capture/backup locking and immutable revision machinery, or
ask the user for a brief save/pause if a consistent snapshot cannot otherwise be
guaranteed. Do not merely copy several changing JSON files sequentially. Work
from the captured revision in an isolated clone based on current remote state;
avoid regressing either city to an older revision.

Commit only validated bundles, manifests and small provenance/evaluation records,
plus the archive recovery tool if appropriate. Follow the existing `.gitattributes`
rule `annotations/** -text` to prevent CRLF conversion from invalidating checksums.
Reconcile `tools/curation_archive.py` with the code-repo implementation rather
than silently shipping inconsistent copies. Add a narrow ignore for atomic
temporary files if needed; do not broadly ignore annotation JSON.

Before and after committing, validate every bundle-listed file's byte length and
SHA256, review identity, city, imagery/acquisition identity, schema, feedback
currentness and canonical tree/mask content. Verify bytes **from the commit**,
not just the working directory. Confirm the pushed remote SHA. If the existing
backup workflow pushes directly to main, make that explicit in the completion
report; otherwise use a review branch rather than assuming blanket merge authority.

## Tests and environment

Do not run local training or install a large GPU stack just to commit code.
The active worktree `.venv` is broken; avoid `uv sync` there. Existing lightweight
reviewer Python (verify before reuse):
`C:/Users/ethan/AppData/Local/uv/cache/builds-v0/.tmpSkdUfj/Scripts/python.exe`.
It has geospatial libraries but not pytest or torch. Some JS behavior tests
invoke Node, so ensure Node is available.

Known lightweight test invocation from `imagery_model/` (select relevant files;
expand coverage for all changed areas):

```powershell
$env:PYTHONPATH="$PWD/src"
uv run --offline --no-project --isolated --with pytest --with numpy --with pandas --with pyarrow --with pydantic --with pyyaml --with pyproj==3.7.2 --with rasterio --with pillow python -m pytest tests/test_confirm_selection.py tests/test_comparison_crowns.py tests/test_selection_position.py tests/test_city_scope.py tests/test_review_order.py -q
```

Also cover snapshot/storage/draft recovery, masks/goal metrics, inference coverage,
backbone/warm-recipe contracts, dataset targets and navigation. Run broader model
tests in an appropriate existing environment; report missing dependencies/skips
honestly. Prior targeted tests passed, but this handoff is not a new full-suite pass.
If touching `src/`, use pnpm, never npm. Run `git diff --cached --check`, review
the entire staged diff and file sizes, and scan secrets before any commit/push.
Ensure shell scripts retain Unix line endings and executable modes where needed.

## Latest run provenance to preserve

- Run `sf-boston-naip-swin-v5-finetune`; v4 `011.ckpt` warm start; 29 epochs,
  selected `016.ckpt` (epoch 17).
- Checkpoint SHA256:
  `9c656fecce19f33cc5885667ce30f7f3695acd5833cea967fa4fe77c72593a22`.
- Goal-aligned F2 at 0.35, crown-aware: SF 37.89% (+0.75 pp vs v4), Boston
  28.16% (-0.35 pp). Fixed 4 m: SF 53.43%, Boston 39.52%.
- Native frozen-label comparisons include data changes, not isolated model uplift.
- Complete inference: 567 SF validation, 636 Boston validation, 2,871 SF train,
  3,330 Boston train. Test remains sealed.
- Result archive recovered and checksum-verified. Both original and recovery GPUs
  terminated; Lambda reported zero active instances. Monitor deleted.
- Report: active source `imagery_model/reports/swin-v5-finetune.md`.
- Large artifacts: main-checkout `imagery_model/artifacts/runs/`, `run-inputs/`,
  and `benchmarks/swin-v5-native-goal/`. Keep them outside Git; retain their hashes
  and retrievable storage provenance. Annotation bucket backup is not proof that
  model checkpoints were uploaded to that bucket.

## Required final handoff back to the user

List each destination repository, branch, commit SHA, push result and PR link if
one was created. Summarize included features, intentionally excluded files,
tests run/skipped, captured annotation revisions and checksum verification.
Explicitly confirm unrelated deletions were not staged, no credentials/large
artifacts were committed, and live curation/reviewer state was left intact.
Report blockers rather than using force-push or destructive cleanup to get green.
