# Paired clean-backbone POC

## Results — completed September 19

Both runs and both-city validation evaluations completed. Archive SHA-256 verified, teardown acknowledged, and Lambda API returned no active instances. Monitor removed after reporting. Total supervised job time was about 78 minutes including startup, GPU contracts and evaluation.

Goal-aligned F2 at confidence 0.35 (unmatched predictions in loss-free regions ignored):

| Model | SF crown-aware 2–4 m | Boston crown-aware 2–4 m | SF fixed 4 m | Boston fixed 4 m |
|---|---:|---:|---:|---:|
| ConvNeXt-Tiny clean | 32.17% | 25.50% | 45.57% | 35.68% |
| Swin-Tiny clean | 34.20% | 26.20% | 48.53% | 36.57% |
| ResNet-34 v21 historical fine-tune | 34.51% | 26.03% | 49.62% | 36.24% |

Swin beats ConvNeXt by 2.02 percentage points in SF and 0.70 pp in Boston on crown-aware F2 under this recipe. The result is not evidence that shifted windows specifically caused the gain: pretrained weights, complete backbone designs and optimization trajectories differ. This is one seed, one threshold, and one common recipe, not a statistically established architecture ranking. ResNet is not a fresh-training control.

ConvNeXt early-stopped after 31 epochs; best checkpoint `018.ckpt` (human epoch 19). Swin reached the 40-epoch cap; best `038.ckpt` (human epoch 39), so there is a reasonable case to test a longer schedule, but no guaranteed uplift.

Attribute metrics on eligible matched detections at fixed 2 m:

| Model/city | Species accuracy | Species macro F1 | DBH MAE (in) | Human-crown radius MAE (m) |
|---|---:|---:|---:|---:|
| ConvNeXt SF | 46.12% | 19.86% | 3.57 | 0.80 (136 crowns) |
| Swin SF | 51.25% | 24.33% | 3.60 | 0.74 (158 crowns) |
| ConvNeXt Boston | 32.08% | 10.97% | 2.75 | 0.85 (66 crowns) |
| Swin Boston | 35.79% | 16.96% | 2.73 | 0.84 (65 crowns) |

Attribute samples differ with detections, so these are conditional metrics, not fixed-tree paired attribute comparisons. Ground-truth counts: SF 12,659; Boston 10,813. Both runs reuse the exact v21 prepared pixels and supervision masks. Paired scorer additionally verifies equality of exported ground-truth tables. Artifacts: `benchmarks/backbone-poc-paired-goal`, `benchmarks/backbone-poc-convnext-native-goal`, `benchmarks/backbone-poc-swin-native-goal`. Ignore the generic scorer report's legacy v16-specific explanatory footer; the metrics JSON and this writeup describe this experiment.

Next experiment: freeze the user's latest curation (not included here), with Swin a promising candidate. Keep any architecture comparison on identical inputs; no new run was launched automatically.

Parent experiment: `sf-boston-naip-backbones-v1`.
Children: `sf-boston-naip-backbones-v1-convnext-tiny`, `sf-boston-naip-backbones-v1-swin-tiny`.

## Contract and scope

- Reuse immutable v21 SF/Boston prepared pixels, labels, masks, taxonomy, normalization and split assignments; no live curation publication or rebuild.
- Fresh ImageNet-pretrained encoder, fresh tree heads and AdamW state for each child. Never read the inherited warm-start instruction as a training initializer. Loading v21 once is only a legacy-checkpoint compatibility test.
- Both modern encoders emit NCHW stride 4/8/16/32 features and an identical new stride-2 detail branch. Shared pyramid decoder and center, DBH, crown, genus and species heads remain unchanged.
- RGBN stem preserves pretrained RGB weights and initializes extra channels from their mean.
- Same seed, learning rate 3e-4, weight decay 1e-4, cosine schedule, 40-epoch cap, validation-loss patience 12, batch 8 × accumulation 4, bf16 mixed precision, dihedral augmentation.
- Full train set; evaluate SF and Boston validation only. Skip full training-gallery inference for this first POC. Test is never evaluated.
- First verify old ResNet checkpoint strict-load compatibility and both modern encoder forward/backward finite-loss contracts on CUDA. CPU-heavy local training is prohibited.
- Sequential runs on one A10, supervised timeout/teardown, hash-verified parent+child collection. Exact instance: `c67fdbcbc30740e0a5e46bbdd34b0be7`.

## Interpretation

Compare goal-aligned F2 using the same frozen labels and masks, plus per-city attribute metrics. This tests a shared practical recipe, not separately optimized architectures. There is no fresh ResNet control in this two-run request; v21 is a historical fine-tuned reference, not a controlled architecture ablation.

## Implementation

- `backbones.py`: input adaptation and hierarchical feature adapter.
- `model.py`: selectable encoder, shared decoder/heads; legacy ResNet parameter names preserved.
- `training.py` and `evaluation.py`: construct the configured backbone.
- `backbone_experiment.py`: clean recipe and paired orchestration over existing prepared data.
- Launcher `--job backbone-poc --prepared-run ...`; collector includes both child run directories even on partial failure.

## Checks before launch

22 lightweight config/recipe/joint checks passed; one pre-existing config equality test failed because curated and uncurated imagery settings differ. GPU contracts run as a fail-fast gate before training. Reviewer was not restarted.
