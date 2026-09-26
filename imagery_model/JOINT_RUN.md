# SF + Boston joint run, September 6, 2026

Run: `sf-boston-naip-curated-joint-v1`. Annotation snapshot: training-data
commit `8333bd7`. All saved reviews were finalized before freezing inputs.

| City | Review scenes | Done | Tree reviews | Corrections | Exclusions |
| --- | ---: | ---: | ---: | ---: | ---: |
| SF | 168 | 165 | 3726 | 114 | 189 |
| Boston | 42 | 42 | 1966 | 162 | 134 |

SF also has one manual center-loss mask region. All 47 points on construction-zone
chip `r000027_c000101` are excluded. All reviewed tree IDs exist in the frozen
inventories. The remote build confirmed every correction, exclusion, and region
was loaded. These review scenes are a selected, sometimes overlapping subset;
they are not the entire city or the entire training dataset.

| City | Training chips | Validation chips | Sealed test chips |
| --- | ---: | ---: | ---: |
| SF | 2872 | 565 | 605 |
| Boston | 3334 | 636 | 802 |

The shared model retains the existing 46-species SF vocabulary and SF training
normalization. This is a fresh training run with the existing pretrained backbone,
dihedral augmentation, and 80-epoch maximum / early stopping. Species outside the
vocabulary retain detection and independently eligible DBH/genus supervision.
Validation labels stay in validation; test is not evaluated. Each city is scored
separately after training using the best validation-loss checkpoint.

## Frozen inputs and outputs

Persistent root: `/lambda/nfs/tree-reporting-dev/urban-tree-ml`.

- `run-inputs/<run>/<city>/`: immutable manifest, reviews, training feedback,
  inventory, and taxonomy used by this run.
- `chips/<run>-<city>/`: separately rebuilt city chips, labels, and build audits.
- `chips/<run>/`: combined manifest with city-qualified IDs and unchanged splits.
- `runs/<run>/`: curation audit, city configs, checkpoints, training results, and
  `evaluation/validation` (SF) / `evaluation/validation-usbos` (Boston).
- `<run>.log` and `<run>-status.json`: job log and completion/termination status.

Earlier run chip directories are preserved. The SF inventory on the old remote
filesystem differed from the local reviewed inventory, so the job explicitly
reads the frozen local inventory instead of overwriting the shared copy.

`python -m urban_tree_ml.joint --experiment <unique-run> <sf-config> <boston-config>`
validates feedback revision hashes and taxonomy compatibility before building.
It requires the frozen inputs above. `lambda/joint.sh` runs it in the GPU container.
The current provisioning helper is specific to this run; change its run name and
paths deliberately before starting another run.

## Supervision

Lambda instance: `c21ada31884a4380bb560c6a8a7722a0`, A10 in Virginia.
The `tree-joint` systemd service runs independently of the desktop session.
`lambda/supervise.py` requests termination of that exact instance after success
or failure, with an eight-hour job limit. An independent `tree-joint-deadline`
timer requests termination at nine hours. Outputs remain on the attached filesystem.
The API key exists only on the instance root disk with owner-only access, outside
the source archive and persistent filesystem. Read-only API authentication was
verified from the host before training.

## Studio reporting

`/coverage` shows current curation by city, publication state, split counts,
verdict totals, and review/evaluation rows with filters and lowest-F2 sorting.
F2 is `5 TP / (5 TP + FP + 4 FN)`, aggregated from saved evaluation counts at the
smallest recorded matching radius and saved confidence threshold. It is not
recomputed after a curation edit. Review scenes and validation chips are labeled
separately; no test scoring is requested by this view.

## Next taxonomy iteration

Use the common cross-city catalog to normalize identities and synonyms, then
select species from usable training examples across both cities. Create genus
support independently of species support, so rare species still teach their
genus. Family fallback requires an explicit family mapping and prediction head.
Version the ordered class mappings with checkpoints; changing them requires
re-encoding targets and resizing/remapping taxonomy heads. Preserve the current
run as the baseline for this later change.
