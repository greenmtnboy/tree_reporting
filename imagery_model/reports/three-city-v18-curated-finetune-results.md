# Three-city v18 curated fine-tune results

The quick fine-tune did not improve the selection objective. All four trained validation checks were worse than the starting v17 baseline, so `parent_baseline_v1` retained the original v17 weights. The v18 selected checkpoint has exactly the same state tensors and validation predictions as the freshly evaluated v17 parent. **Keep v17 as the next-training parent; no live model switch or city publishing occurred.**

Training stopped normally after 1,132 optimizer steps and four nonimproving validation checks. The run completed all nine inference cohorts, the collector verified the archive and acknowledged teardown, and exact instance `0aa36fcf7c054546a6665501ac9d7542` was API-verified terminated with no IP. No retry, extension or recovery instance was used.

## Scope and frozen supervision

Run: `sf-boston-nyc-naip-swin-30cm-v18-curated-finetune`. Parent: `sf-boston-nyc-naip-swin-30cm-v17-curated-finetune`, `000-0000282.ckpt`, SHA256 `77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df`. Swin Tiny RGBN, 99 output classes, 512 px at 30 cm; SF 60 cm source resampled to 30 cm, Boston/NYC native 30 cm. Weights-only initialization with a fresh optimizer; LR 3e-5, batch 2 × accumulation 16, bf16, fixed_pair_v1/source_passes_v1, patience 4, maximum 80 source passes. Loss weights: center 1, DBH .5, crown 1, genus .5, species .5, explicit single-tree 1.

Validation includes only explicitly full-chip `more_done` reviews intersected with the actual validation manifest. Frozen reviewed IDs were 220/90/9; two SF and two Boston IDs were outside the validation manifest. Ordinary Done was not admitted. The cohort was frozen before allocation; the newly marked more_done tiles for this run were training tiles. NYC training remains six curated tiles; the 93 test tiles and all sealed test pixels were untouched.

| City | Validation tiles | Validation targets | Training tiles | Training targets |
| --- | --- | --- | --- | --- |
| San Francisco | 218 | 6911 | 4468 | 141938 |
| Boston | 88 | 5060 | 4577 | 100725 |
| New York City | 9 | 512 | 6 | 677 |

## Selection and learning curve

| Step | Source passes (step / 283) | Validation loss | UTC |
| --- | --- | --- | --- |
| 0 | 0.000 | 3.435403824 | 2026-11-01 18:04:48 UTC |
| 282 | 0.996 | 3.451264143 | 2026-11-01 18:11:05 UTC |
| 566 | 2.000 | 3.470656157 | 2026-11-01 18:17:24 UTC |
| 848 | 2.996 | 3.497370243 | 2026-11-01 18:23:41 UTC |
| 1132 | 4.000 | 3.481709242 | 2026-11-01 18:30:02 UTC |

The best trained candidate was `000-0000282.ckpt`, loss 3.451264143, versus baseline 3.435403824 (0.462% worse). Selected `parent-baseline.ckpt` SHA256 `581c45ea723a1e7d37d8432493d17a596bb4293f6f5df4d11e4ff64dd6dee938` differs as a serialized checkpoint file, but every model state tensor exactly equals the original parent. `eligible_for_parent_advance=false`; both parent records therefore remain v17. True `last.ckpt` is step 1,132, and the three best trained checkpoints plus the baseline are retained.

The four trained checks show no improvement in the aggregate objective. This is a failed improvement attempt, not proof that Tiny has saturated or that more curation cannot help. There is only one seed/run, four source-equivalent passes, and a short fine-tune schedule. Below are ordinary least-squares slopes over the final three validation checks and all five checks including the baseline; positive means increasing loss.

| Component | Last 3 / 1,000 steps | Last 3 / hour | All 5 / 1,000 steps | All 5 / hour |
| --- | --- | --- | --- | --- |
| loss | 0.019440 | 0.052226 | 0.048999 | 0.131911 |
| center_loss | 0.010479 | 0.028168 | 0.041147 | 0.110766 |
| single_tree_peak_loss | 0.000131 | 0.000350 | -0.000210 | -0.000566 |
| dbh_loss | -0.004804 | -0.012915 | 0.000967 | 0.002597 |
| crown_loss | -0.000902 | -0.002424 | -0.000135 | -0.000363 |
| genus_loss | 0.023375 | 0.062832 | 0.009194 | 0.024780 |
| species_loss | 0.000896 | 0.002355 | 0.006233 | 0.016773 |

## Compute and completion

One A10 in us-east-1. Allocation requested at 2026-11-01 16:56:32 UTC; supervisor finished at 2026-11-01 18:35:48 UTC. Allocation-to-job-finish was 5955.725 s; supervisor elapsed time was 5700.980 s. Baseline-completion to final validation was 1513.719 s, averaging 0.7478 optimizer steps/s including validation. These are recorded elapsed intervals, not exact billed hours; the provider termination timestamp and billing total were unavailable. Sampled training GPU memory was 2,375 MiB and utilization 83–86%, not a measured peak.

The optimization cutoff was 19:56:32 UTC, supervisor cutoff 20:26:32 UTC, and absolute termination deadline 20:56:32 UTC. The run stopped through normal early stopping well before these cutoffs. Both independent timers, actual Docker cutoff propagation, upload/storage checks, required Rust, CUDA focal/crown guards and all three 512 px batch-2 backward preflights were verified. All backward probes had zero bad/missing gradients. Forty-five targeted local tests passed before launch.

| Inference cohort | Tiles | Targets | Predictions |
| --- | --- | --- | --- |
| validation | 218 | 6911 | 96217 |
| parent-validation | 218 | 6911 | 96217 |
| train-ussfo | 4468 | 141938 | 2024670 |
| validation-usbos | 88 | 5060 | 38131 |
| parent-validation-usbos | 88 | 5060 | 38131 |
| train-usbos | 4577 | 100725 | 1957214 |
| validation-usnyc | 9 | 512 | 4092 |
| parent-validation-usnyc | 9 | 512 | 4092 |
| train-usnyc | 6 | 677 | 2785 |

## Frozen validation detection scores

The following numbers apply identically to the v18 selected model and the fresh paired v17 parent; every paired delta is zero. They do not describe the rejected trained candidate. Both models were scored with the same decoder, frozen labels and masks using the original v18 archived source in Linux. All 315 chips and both sets of stored prediction-mask values reproduced exactly, including NYC. No mask guards were relaxed. Windows historical boundary differences were avoided.

Best F2 thresholds are tuned on this validation cohort and are descriptive, not held-out performance. Crown-aware matching uses the scorer’s half-crown-radius policy bounded to 2–4 m; fixed-distance policies are shown separately. P, R, F1 and F2 are percentages.

| City | Policy | Best F2 threshold | P | R | F1 | F2 |
| --- | --- | --- | --- | --- | --- | --- |
| San Francisco | Crown-aware | 0.14 | 73.597 | 77.804 | 75.642 | 76.924 |
| San Francisco | Fixed 2 m | 0.14 | 72.379 | 76.516 | 74.390 | 75.651 |
| San Francisco | Fixed 4 m | 0.14 | 81.070 | 85.704 | 83.323 | 84.735 |
| Boston | Crown-aware | 0.12 | 49.250 | 66.146 | 56.461 | 61.899 |
| Boston | Fixed 2 m | 0.12 | 43.629 | 58.597 | 50.017 | 54.834 |
| Boston | Fixed 4 m | 0.12 | 62.331 | 83.715 | 71.457 | 78.340 |
| New York City | Crown-aware | 0.13 | 70.588 | 75.000 | 72.727 | 74.074 |
| New York City | Fixed 2 m | 0.11 | 65.499 | 73.047 | 69.067 | 71.401 |
| New York City | Fixed 4 m | 0.11 | 78.459 | 87.500 | 82.733 | 85.529 |

Fixed threshold comparison (identical for both):

| City | Policy | Threshold | P | R | F1 | F2 |
| --- | --- | --- | --- | --- | --- | --- |
| San Francisco | Crown-aware | 0.13 | 72.442 | 78.049 | 75.141 | 76.860 |
| San Francisco | Crown-aware | 0.35 | 93.645 | 42.859 | 58.805 | 48.073 |
| San Francisco | Fixed 2 m | 0.13 | 71.246 | 76.762 | 73.901 | 75.591 |
| San Francisco | Fixed 2 m | 0.35 | 93.076 | 42.599 | 58.447 | 47.781 |
| San Francisco | Fixed 4 m | 0.13 | 79.828 | 86.008 | 82.803 | 84.696 |
| San Francisco | Fixed 4 m | 0.35 | 97.629 | 44.682 | 61.306 | 50.118 |
| Boston | Crown-aware | 0.13 | 51.436 | 65.138 | 57.482 | 61.843 |
| Boston | Crown-aware | 0.35 | 84.000 | 7.885 | 14.417 | 9.631 |
| Boston | Fixed 2 m | 0.13 | 45.552 | 57.688 | 50.907 | 54.770 |
| Boston | Fixed 2 m | 0.35 | 83.579 | 7.846 | 14.345 | 9.582 |
| Boston | Fixed 4 m | 0.13 | 65.044 | 82.372 | 72.689 | 78.205 |
| Boston | Fixed 4 m | 0.35 | 96.000 | 9.012 | 16.477 | 11.007 |
| New York City | Crown-aware | 0.13 | 70.588 | 75.000 | 72.727 | 74.074 |
| New York City | Crown-aware | 0.35 | 100.000 | 1.758 | 3.455 | 2.188 |
| New York City | Fixed 2 m | 0.13 | 68.015 | 72.266 | 70.076 | 71.373 |
| New York City | Fixed 2 m | 0.35 | 100.000 | 1.758 | 3.455 | 2.188 |
| New York City | Fixed 4 m | 0.13 | 80.882 | 85.938 | 83.333 | 84.877 |
| New York City | Fixed 4 m | 0.35 | 100.000 | 1.758 | 3.455 | 2.188 |

## Taxonomy and vegetation

Genus, species and top-5 accuracies below are conditional on distance-matched detections under the crown-aware policy. They are not taxonomy recall over every ground-truth tree. Vegetation categories are separate from species/genus and exempt from the single-tree ownership loss. Support is too small to establish vegetation category quality. Full metrics, including macro scores, joint taxonomy, crown error and DBH error, are retained in `best.json` and `fixed.json`.

| City | Point | Threshold | Genus n | Genus % | Species n | Species % | Top-5 % | Vegetation n | Vegetation % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| San Francisco | best F2 | 0.14 | 4015 | 51.482 | 3631 | 55.770 | 79.179 | 1 | 0.000 |
| Boston | best F2 | 0.12 | 1656 | 40.278 | 1212 | 46.287 | 70.875 | 2 | 0.000 |
| New York City | best F2 | 0.13 | 285 | 20.702 | 248 | 16.935 | 51.613 | 0 | n/a |
| San Francisco | fixed | 0.13 | 4026 | 51.416 | 3639 | 55.730 | 79.115 | 1 | 0.000 |
| San Francisco | fixed | 0.35 | 2326 | 51.462 | 2105 | 57.340 | 81.330 | 0 | n/a |
| Boston | fixed | 0.13 | 1638 | 40.232 | 1199 | 46.372 | 70.976 | 1 | 0.000 |
| Boston | fixed | 0.35 | 209 | 28.230 | 160 | 28.125 | 57.500 | 0 | n/a |
| New York City | fixed | 0.13 | 285 | 20.702 | 248 | 16.935 | 51.613 | 0 | n/a |
| New York City | fixed | 0.35 | 7 | 28.571 | 5 | 20.000 | 60.000 | 0 | n/a |

## Explicit single-tree occupancy

This is an ownership-region occupancy proxy: covered groups have at least one predicted peak, duplicate groups have multiple peaks, and excess peaks count predictions beyond one per group. Occupancy P = covered groups / peaks and R = covered groups / groups. These are not distance-matched detection P/R. Materialized ownership groups may represent clipped or overlapping portions rather than unique drawn regions. Both models have identical occupancy results.

| City | Threshold | Groups | Covered | Missed | Duplicate groups | Excess peaks | Occupancy P | Occupancy R |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| San Francisco | 0.13 | 4 | 3 | 1 | 2 | 2 | 60.000 | 75.000 |
| San Francisco | 0.35 | 4 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| Boston | 0.13 | 19 | 19 | 0 | 15 | 25 | 43.182 | 100.000 |
| Boston | 0.35 | 19 | 0 | 19 | 0 | 0 | n/a | 0.000 |
| New York City | 0.13 | 2 | 2 | 0 | 2 | 4 | 33.333 | 100.000 |
| New York City | 0.35 | 2 | 0 | 2 | 0 | 0 | n/a | 0.000 |

## Interpretation and provenance

Retaining v17 means this run contributes new diagnostics but no new selected model. The same model on identical inputs produces zero paired differences, so a paired spatial bootstrap would be degenerate at zero; that is not evidence of zero uncertainty about generalization. The curated validation sample is nonrandom, NYC has only nine tiles, and repeated selection can overfit validation. Training curation and optimization are coupled; this experiment does not isolate the causal effect of curation. Full-train inference is diagnostic and is not a generalization score.

- Completion audit: `artifacts/sf-boston-nyc-naip-swin-30cm-v18-curated-finetune-completion-audit.json` (selection, state equality, true last, nine cohorts, exact API termination).
- Results archive SHA256: `f0d2629a02de8237ed47455e1a852a479b7e1ada0f3cb099b3d7abe343e8cfaf`; collector session 98936 exited 0 after checksum verification and download ACK.
- Source archive SHA256: `2245622dadef7bfbb23ff72229e70482bda46fb9a27fe8593ddabd1e11b440b9`.
- Inputs archive SHA256: `eca861704e5543493c72bfe80f5a611c115da79ce5f96a2f5b30936412749b99`.
- Baseline input signature: `76ca9b0465f76ec609a51ee04aecc7f710e7fdbce87eaf161be6410dde1c5bf9`.
- CPU image: `tree-v18-audit-cpu`, SHA256 `737cbb3cc9ee44e868c83d4492158dfc8fb068548ace59aef9b0a9c784aec9cf`; dependency lock and original source preserved. VRT overlays change only source paths.
- Strict comparison: `artifacts/benchmarks/v18-linux-reproduction/output/frozen-comparison/` (`provenance.json`, `best.json`, `fixed.json`, `sweep.json`, `COMPLETE`).
- Occupancy: `artifacts/benchmarks/v18-linux-reproduction/output/occupancy/`; independent mask/runtime receipt: `output/both-models-mask-audit.json`.
- Parent records remain v17. Live curator, model defaults, city publications and annotations were unchanged.
