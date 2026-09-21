# v19 fine-tune

## Results

Completed successfully; archive hash verified, results collected, teardown
acknowledged, and Lambda reports no active instances. Best checkpoint: `012.ckpt`.

Goal-aligned validation F2, confidence 0.35, each run's own frozen labels and masks:

| Matching policy | SF v18 → v19 | Boston v18 → v19 |
|---|---:|---:|
| Crown half-radius, clipped 2–4 m | 34.94% → 33.34% | 22.18% → 24.19% |
| Fixed 2 m | 34.64% → 33.00% | 21.77% → 23.77% |
| Fixed 4 m | 49.35% → 48.98% | 30.78% → 33.65% |

Boston improved on the updated answer key; SF regressed. Boston validation
targets changed from 12,864 to 11,877 across the same 636 chips. SF remained
12,659 targets across 567 chips. Do not attribute the full Boston uplift to
model learning: paired inventory-relative diagnostics indicate most of that
metric's increase comes from corrected labels, with a smaller model gain on
the common new labels. That decomposition is NOT a goal-aligned decomposition,
because masks were not remapped across runs. SF truth is unchanged, although
full mask equality has not been independently verified.

Raw goal metrics/provenance: `artifacts/benchmarks/v19-native-goal/` and
`artifacts/benchmarks/v18-v16-crown-center/`. The native scorer's generated
REPORT footer describes the older v16 experiment; use this writeup instead.
Paired diagnostic: `artifacts/benchmarks/sf-boston-naip-curation-v19-finetune-vs-sf-boston-naip-curation-v18-finetune/`.

## Launch provenance

Run: `sf-boston-naip-curation-v19-finetune`.

Published both cities and froze current curation, including recovered Boston
scene-521 (82 decisions, 22 regions, second-pass completion). Compared with v18,
SF has no changed projected tree reviews and 335 regions; Boston has 2,478
changed projected reviews and 1,743 regions (previously 1,433). These are review
projection counts, not unique trees or necessarily changed training targets.

Warm start: v18 `010.ckpt`, weights only with a fresh optimizer, LR 0.00003,
40-epoch cap and validation early stopping. Crown head enabled; inherited
crown-scaled center policy (fraction 0.3, maximum sigma 3 m, estimated scale 0.5).
Test remains sealed. Report goal-aligned metrics and separate label/model drift.

Frozen inputs and checksums: `artifacts/run-inputs/sf-boston-naip-curation-v19-finetune/`.
Instance receipt: `artifacts/sf-boston-naip-curation-v19-finetune-instance.json`.
Exact allocated Lambda instance: `0e5dcc43affe458e8a4594633e1ef395`.
The launcher installs supervised termination and an independent deadline;
completion monitoring is enabled. Consult the receipt/status for current state.
