# Swin v5 fine-tune — recovered results

`sf-boston-naip-swin-v5-finetune` continued Swin v4's `011.ckpt`
from the latest published SF/Boston curation snapshot. Changed canonical
tree records versus v4: SF 2,013; Boston 133. RGBN Swin-Tiny, crown head,
crown-aware center policy, weights-only warm start, fresh optimizer,
learning rate 3e-5, 80-epoch cap, early-stopping patience 12.

Training completed successfully after 29 epochs; selected `016.ckpt`
(human epoch 17). Checkpoint SHA256:
`9c656fecce19f33cc5885667ce30f7f3695acd5833cea967fa4fe77c72593a22`.

## Goal-aligned validation, confidence 0.35

| City / tolerance | v4 F2 | v5 F2 | Change |
|---|---:|---:|---:|
| SF crown-aware | 37.14% | 37.89% | +0.75 pp |
| Boston crown-aware | 28.51% | 28.16% | -0.35 pp |
| SF fixed 4 m | 51.25% | 53.43% | +2.19 pp |
| Boston fixed 4 m | 40.09% | 39.52% | -0.57 pp |

Crown-aware matching uses half the target crown radius clamped to 2–4 m,
with a 2 m fallback. Matching is one-to-one. Human-added crowns are eligible
targets; unmatched zero-loss-mask predictions are ignored, not credited.
No inventory-only scoring fallback. Each run uses its own frozen labels;
these are workflow results, not a controlled model-only attribution.

V5 crown-aware precision/recall: SF 58.53% / 34.82%; Boston 58.76% / 24.91%.
Validation target totals: SF 12,588; Boston 10,556.

## Recovery and coverage

The original instance terminated before local collection succeeded. Its
persistent filesystem retained the complete run and supervisor exit code 0.
A user-authorized recovery instance downloaded the result archive with
SHA256 verification and extraction. Recovery started no training and did
not modify annotations or restart the reviewer.

All inference manifests report complete, with list lengths matching counts:
SF validation 567; Boston validation 636; SF training 2,871; Boston training
3,330. Test remains sealed.

Recovery instance: `10deca661c704725a0fa36e35bcf173d`; automatic termination
requested immediately after collection, with a separate 45-minute watchdog.
Lambda API subsequently confirmed it terminated and zero active instances.

Metrics and source hashes:
`artifacts/benchmarks/swin-v5-native-goal/{metrics,provenance}.json`.
Use this report instead of the generic scorer's historical experiment prose.
