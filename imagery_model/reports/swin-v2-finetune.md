# Swin v2: latest-curation continuation

## Completed results

Training early-stopped after 16 epochs (80 maximum); the selected checkpoint
was `003.ckpt`, human epoch 4, with validation loss 4.502189. Twelve later
epochs did not improve that loss. All losses checked were finite.

Goal-aligned F2 at confidence 0.35 (unmatched zero-loss-mask predictions are
ignored, not credited as true positives):

| Run | SF crown-aware | Boston crown-aware | SF 4 m | Boston 4 m |
|---|---:|---:|---:|---:|
| ResNet v21 | 34.51% | 26.03% | 49.62% | 36.24% |
| Swin POC | 34.20% | 26.20% | 48.53% | 36.57% |
| Swin v2 | 34.71% | 26.98% | 49.43% | 37.76% |

Crown-aware means half the target crown radius, clamped to 2–4 m, with 2 m
fallback. Compared with the Swin POC, native F2 improved +0.51/+0.78 percentage
points (SF/Boston) crown-aware and +0.90/+1.18 points at 4 m. These are native
run scores, **not a frozen-common-label model-only uplift**: v2 uses newly
published curation. SF has 12,661 validation targets versus 12,659 before;
Boston has 10,813 in both, which alone does not prove unchanged coordinates,
attributes, or masks. Do not attribute all changes to optimization.

Both training coverage manifests are complete: 2,871 SF chips and 3,330 Boston
chips (6,201 total). Both validation exports are present. Results downloaded
and verified, collector acknowledged teardown, and Lambda lists no active
instances. Test remained sealed. The model gallery route for this run returned
HTTP 200 without restarting the live reviewer.

Machine-readable scoring and provenance are in
`artifacts/benchmarks/swin-v2-native-goal/metrics.json` and `provenance.json`.

## Launch recipe

Run: `sf-boston-naip-swin-v2-finetune`.

Both city saves were idle/error-free; `/api/finalize?city=all` succeeded for
SF and Boston before the immutable run-input freeze. This does not reuse the
older POC label snapshot. Subsequent curation is independent of this run.

- Parent: `sf-boston-naip-backbones-v1-swin-tiny`, best checkpoint `038.ckpt`.
- SHA256: `2b2f3155b99eff7b2ccc454ca0746c3f9ab6e88dc37799a0d272db57480c6bd9`.
- Swin-Tiny RGBN encoder, shared prediction heads including crown radius.
- Weights-only continuation; fresh optimizer and cosine schedule, LR 0.00003.
- Maximum 80 epochs; validation-loss early stopping patience 12.
- Batch 8, accumulation 4 (effective 32), inherited model/head configuration.
- Inherited crown-center target policy; new chips built from frozen curation.
- Both validation evaluations, then **all training-chip predictions** in both
  cities before collection/teardown. No test evaluation.

The launcher now inherits the parent's model and batch recipe explicitly;
three lightweight tests cover Swin continuation, mixed-parent rejection, and
legacy manifest compatibility. An epoch cap is not a promise to train all 80
epochs. This is not a fresh ImageNet-only backbone comparison.

Report goal-aligned F2 separately for crown-aware and fixed 4 m matching.
Latest labels differ from the POC: native score changes cannot be attributed
solely to training. Keep the ResNet baseline available pending results.
