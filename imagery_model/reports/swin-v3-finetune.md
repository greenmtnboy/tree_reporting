# Swin v3 fine-tune — completed

Run `sf-boston-naip-swin-v3-finetune` continued Swin v2's best `003.ckpt`
using the latest saved/published SF and Boston curation. Both feedback files
changed from v2. Weights-only continuation, fresh optimizer, LR 3e-5, cosine
80-epoch cap, batch 8 with accumulation 4, validation-loss patience 12.
RGBN Swin-Tiny and crown head/center policy retained; test not evaluated.

Training early-stopped after **22 epochs**, selecting `009.ckpt` (human epoch
10), validation loss **4.517155**. Twelve subsequent epochs did not improve it.
The run exited successfully. All four inference coverage manifests are complete:
567 SF validation, 636 Boston validation, 2,871 SF train, 3,330 Boston train.
Collector verified the downloaded archive and acknowledged GPU teardown.
Lambda teardown confirmed September 20 at 06:15 UTC: no active instances remain.

## Goal-aligned F2 at confidence 0.35

| City / matching | Swin v2 | Swin v3 | Change |
|---|---:|---:|---:|
| SF crown-aware | 34.71% | 35.40% | +0.70 pp |
| Boston crown-aware | 26.98% | 27.45% | +0.47 pp |
| SF fixed 4 m | 49.43% | 49.63% | +0.20 pp |
| Boston fixed 4 m | 37.76% | 38.43% | +0.67 pp |

Crown-aware matching uses half the target radius, clamped to 2–4 m (2 m
fallback). Unmatched predictions in zero-loss-mask regions are ignored, not
credited. All matches are one-to-one. These are **native updated-label scores**,
not model-only controlled uplift. SF validation targets changed from 12,661 to
12,630; Boston remains 10,813, which does not establish equality of masks or
locations. No statistical significance claim from these small differences.

V3 crown-aware precision/recall: SF 59.43%/32.15%, Boston 59.62%/24.18%.
V3 fixed-4m precision/recall: SF 83.32%/45.08%, Boston 83.47%/33.86%.

Scoring and prediction/truth hashes:
`artifacts/benchmarks/swin-v3-native-goal/metrics.json` and `provenance.json`.
Downloaded model and all training overlays:
`artifacts/runs/sf-boston-naip-swin-v3-finetune/`.
