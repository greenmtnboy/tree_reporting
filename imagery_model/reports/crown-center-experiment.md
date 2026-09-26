# v16: crown-size-aware center targets

Baseline: `sf-boston-naip-curation-v15-finetune` best `005.ckpt`.
Candidate: `sf-boston-naip-curation-v16-crown-center`.

This experiment reuses the **exact frozen v15 city inputs**; live curation is
not read or published. Same model, loss functions, optimizer configuration,
augmentation, taxonomy, split and training data. Fresh optimizer, learning rate
0.00003, maximum 40 epochs, existing early stopping on unchanged validation loss.

## Training change

Only training center heatmaps change. Let the original sigma be `s` output
pixels and output resolution be `g` meters per pixel. Desired sigma is
`max(s, min(0.3 * target_crown_radius_m, 3.0) / g)`.
Human crowns use this sigma directly. Estimated crowns use half the increase
over the original sigma. Unknown/invalid/small crowns retain the original width.
At the current 0.6 m imagery and stride 2, original sigma is 1.8 m, capped at
3 m for measured crowns. This primarily changes crowns over 12 m in diameter.

The exact center remains the only full positive. Our CenterNet focal loss uses
the Gaussian skirt to reduce nearby negative penalties; this does not create
additional positive centers or a flat positive disk. Detection masks, attribute
labels and collision exclusions do not change. Validation/test target heatmaps
retain their original widths. Test is not evaluated.

## Scoring

`python -m urban_tree_ml.crown_center_score` compares the same frozen validation
labels with both models. It reports goal-aligned F2 at confidence 0.35 for:

- Fixed 2 m matching.
- Fixed 4 m matching.
- Size-aware matching: half the **target** radius, clipped to 2–4 m;
  unknown/invalid radius uses 2 m.

Matching is confidence-ordered nearest eligible unused target, one-to-one.
Predicted radius cannot change the model's tolerance. Human overrides already
take precedence over allometric labels. Unmatched predictions in zero-mask
regions are ignored, not true positives. Historical scores are never overwritten.

The baseline-only report is `artifacts/benchmarks/v16-crown-center-v15-baseline`;
the paired output should be `artifacts/benchmarks/v16-v15-crown-center`.
Strict inventory metrics from the collector remain diagnostics only.

This controls label drift, but **not additional optimization time**: without a
parallel unchanged-target fine-tune, improvements cannot be attributed solely
to target broadening. Dense overlapping crowns remain a particular audit case.

## Checks

23 target/chip/config tests passed, including exact-center peak, unchanged masks
and attributes, missing/small crowns, radius cap, reduced estimated adjustment,
collision exclusion, one-to-one matching and predicted-radius independence.
An unrelated existing config test (`test_curated_config_changes_only_experiment_identity`)
still fails because citywide and curated imagery settings differ.

No reviewer restart is needed; new curation can continue independently.
