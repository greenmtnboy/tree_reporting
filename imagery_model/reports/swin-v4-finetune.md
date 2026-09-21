# Swin v4 fine-tune — completed

Run `sf-boston-naip-swin-v4-finetune` continued Swin v3's `009.ckpt`
with freshly published SF/Boston curation (1,484 SF and 1,124 Boston changed
tree records). RGBN Swin-Tiny, crown head and crown-aware center policy,
LR 3e-5, fresh optimizer, batch 8 / accumulation 4, 80-epoch cap.
Training stopped after 24 epochs; selected `011.ckpt` (human epoch 12).

Collector verified the result archive checksum and acknowledged teardown.
Lambda API confirmed no active instances at September 20, 17:11 UTC.
Supervisor exit code 0. Complete inference coverage: 567 SF validation,
636 Boston validation, 2,871 SF train and 3,330 Boston train chips.
Test remains sealed. Reviewer was not restarted.

## Goal-aligned F2, confidence 0.35

| City / tolerance | Swin v3 | Swin v4 | Change |
|---|---:|---:|---:|
| SF crown-aware | 35.40% | 37.14% | +1.74 pp |
| Boston crown-aware | 27.45% | 28.51% | +1.06 pp |
| SF fixed 4 m | 49.63% | 51.25% | +1.61 pp |
| Boston fixed 4 m | 38.43% | 40.09% | +1.67 pp |

Crown-aware tolerance = half target crown radius, clamped to 2–4 m,
with 2 m fallback. One-to-one matches; unmatched zero-loss-mask predictions
are ignored, not credited. No inventory-only scoring fallback.

These compare each run on its own frozen curation, measuring the combined
model/data workflow, not isolated model-only uplift. Validation target totals
changed from 12,630 to 12,588 (SF) and 10,813 to 10,556 (Boston).
V4 crown-aware precision/recall: SF 61.02%/33.83%; Boston 58.83%/25.26%.

Metrics and source hashes are saved under
`artifacts/benchmarks/swin-v4-native-goal/{metrics,provenance}.json`.
Use this report rather than the generic scorer's experiment-specific prose.
