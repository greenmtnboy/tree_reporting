# v20 retry results

Successful completion, hash-verified archive collected, teardown acknowledged,
and no active Lambda instances confirmed. Trained for 27 epochs; selected
checkpoint `014.ckpt` (epoch 15). Test remains sealed.

All inference receipts are complete: SF train 2,871 / validation 567;
Boston train 3,330 / validation 636.

Goal-aligned F2, confidence 0.35, each run's own frozen labels and masks:

| Matching policy | SF v19 → v20 retry | Boston v19 → v20 retry |
|---|---:|---:|
| Crown half-radius, clipped 2–4 m | 33.34% → 34.84% | 24.19% → 25.11% |
| Fixed 2 m | 33.00% → 34.48% | 23.77% → 24.65% |
| Fixed 4 m | 48.98% → 49.89% | 33.65% → 35.30% |

Both cities improved on this native goal-aligned comparison. Boston validation
targets changed from 11,877 to 11,472; SF has 12,659 in both runs. Native deltas
combine model and answer-key/mask changes, so they are not pure model uplift.
Do not assume equal SF counts prove identical geometry or masks.

Goal metric files and source hashes: `artifacts/benchmarks/v20-retry1-native-goal/`
and `artifacts/benchmarks/v19-native-goal/`. The scorer's generated REPORT footer
describes an older experiment; use this writeup for the interpretation.
Paired model/label diagnostic: `artifacts/benchmarks/v20-retry1-v19-paired/`.
That diagnostic is inventory-relative, not a goal-aligned decomposition, because
loss masks are not reprojected between runs. Do not use it as the headline score.

The paired diagnostic attributes SF's inventory-relative gains entirely to the
changed predictions (zero measured label effect). Boston shows a mix: label
corrections dominate at 2 m; both labels and predictions contribute at 4 m.
This does not establish how much training curation caused the model change,
nor decompose the goal-aligned improvements above.
