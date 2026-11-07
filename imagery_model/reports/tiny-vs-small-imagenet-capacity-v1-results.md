# Tiny versus Small: ImageNet capacity comparison v1

Completed 2026-10-27. Both runs, collection, strict Linux reconstruction, scoring, occupancy diagnostics and geographic uncertainty audits passed. Both exact GPU instances are API-confirmed terminated. No model was promoted; v17 remains the next-training parent and the live curator was left unchanged.

**There is no consistent quality winner across cities and metrics.** Tiny has higher crown-aware F2 in SF and higher fixed-4 m best F2 in all three cities. Small improves crown-aware and fixed-2 m F2 in Boston and NYC, but conditional species top-1 falls in both cities. Tiny provides 26.8% greater measured optimizer-step throughput and uses less sampled GPU memory. This single paired run does not establish that Small’s extra cost is justified generally.

Primary best-threshold crown-aware results below are validation-selected descriptive results, not sealed-test estimates. Differences are Small minus Tiny in percentage points (pp).

| City | Tiny F2 % | Small F2 % | Difference pp | Paired block 95% interval, pp |
| --- | --- | --- | --- | --- |
| SF | 77.19 | 76.43 | -0.75 | [-1.46, -0.10] |
| Boston | 60.47 | 61.44 | +0.97 | [-0.06, +2.21] |
| NYC | 69.15 | 71.32 | +2.17 | withheld: only 2 blocks |

SF’s interval favors Tiny under this block design. Boston’s best-threshold interval includes zero; NYC has only nine chips in two geographic blocks, too little support for a stable spatial interval. Small’s modestly lower aggregate validation loss (0.657%) is not evidence of uniformly better detection or taxonomy.

## Frozen experiment and admission

Both models started with torchvision ImageNet-1K backbone weights and fresh tree heads and optimizer (`imagenet_fresh_heads_fresh_optimizer_v1`, actual `initial_checkpoint=null`). No v17 or other tree weights were inherited; `base-start.json` is present and `warm-start.json` absent. The shared archived source and 25 common frozen input files were verified; no newer curation was substituted.

Recipe: Swin Tiny / Swin Small, RGBN, 99 classes, 512 px at 30 cm model resolution; SF 60 cm source resampled to 30 cm, Boston/NYC native 30 cm. Batch 2 × accumulation 16, bf16, learning rate 3e-4, two source-pass linear warmup followed by cosine, maximum 80 source passes, patience 12 validation checks, `fixed_pair_v1` augmentation and `source_passes_v1` budget. Loss weights: genus/species 0.5/0.5, center 1, DBH 0.5, crown 1, explicit single-tree 1; vegetation groups exempt from the single-tree rule.

| City | Train chips / targets | Frozen reviewed IDs | Admitted validation chips / targets | 768 m blocks |
| --- | --- | --- | --- | --- |
| SF | 4,468 / 141,493 | 220 | 218 / 6,911 | 27 |
| Boston | 4,577 / 100,733 | 90 | 88 / 5,060 | 24 |
| NYC | 6 / 677 | 9 | 9 / 512 | 2 |

Only explicit full-chip `scene_reviews.more_done is True` reviews were admitted after intersection with the actual validation manifest; ordinary Done was insufficient. Two SF and two Boston reviewed IDs lay outside validation and were excluded. The cohort totals 315 chips and 12,483 targets. NYC used the original 19 validation-pixel IDs with rebuilt targets and then the frozen nine-chip review filter; all nine admitted image arrays matched original v8 pixels. NYC’s 93 sealed test chips and protected SF refresh validation/test were untouched. Full-train inference was intentionally omitted to reserve the fixed collection deadline.

44 targeted predeployment tests passed. Each architecture’s actual A10 preflight passed synthetic bf16, synthetic fp32 and ImageNet bf16 backward probes at 512 px/batch 2 with zero bad or missing gradients. Required Rust import, CUDA focal/crown guards, storage read/write, upload hashes and independent absolute deadline timers were verified.

## Scoring and strict reproduction

All six collected inference cohorts match the admitted IDs and target counts. The original archived source and locked Linux dependencies reproduced every target and both models’ stored mask values exactly (maximum difference 0.0). A prior Windows probe failed two NYC output-y coordinates and was rejected; no equality or mask guard was relaxed. Linux runtime: Python 3.13.12, NumPy 2.5.2, pandas 3.0.5, Rasterio 1.5.1 / GDAL 3.12.4, pyproj 3.7.2, required Rust backend. Pair-specific VRT overlays changed file paths only. Inputs were mounted read-only, with separate writable benchmark output; each audit container was limited to 2 CPUs and 4 GiB.

Both models use the same `equal_score_greedy_row_column_v1` decoder. Confidence-ordered greedy matching assigns at most one detection per target. Unmatched detections with nonpositive frozen scoring-mask value are ignored rather than counted as false positives. Crown-aware tolerance is half the labelled crown radius clamped to 2–4 m (missing radius falls back to 2 m); fixed policies use 2 m or 4 m. F2 weights recall more strongly: 5TP/(5TP+FP+4FN). Confidence sweeps use 0.01–0.95 in 0.01 increments, breaking best-F2 ties toward the higher threshold. Average precision was not computed.

| City | Tiny decoded predictions | Small decoded predictions |
| --- | --- | --- |
| SF | 100,519 | 96,172 |
| Boston | 37,835 | 37,296 |
| NYC | 3,570 | 3,330 |

### Best F2 operating points

| City | Policy | Model | Threshold | P % | R % | F1 % | F2 % | TP / FP / FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | Crown-aware | Tiny | 0.13 | 72.25 | 78.53 | 75.26 | 77.19 | 5427 / 2084 / 1484 |
| SF | Fixed 2 m | Tiny | 0.14 | 72.18 | 76.89 | 74.46 | 75.90 | 5314 / 2048 / 1597 |
| SF | Fixed 4 m | Tiny | 0.12 | 77.93 | 86.82 | 82.14 | 84.88 | 6000 / 1699 / 911 |
| SF | Crown-aware | Small | 0.12 | 72.09 | 77.60 | 74.75 | 76.43 | 5363 / 2076 / 1548 |
| SF | Fixed 2 m | Small | 0.12 | 70.86 | 76.27 | 73.46 | 75.12 | 5271 / 2168 / 1640 |
| SF | Fixed 4 m | Small | 0.12 | 79.92 | 86.02 | 82.86 | 84.73 | 5945 / 1494 / 966 |
| Boston | Crown-aware | Tiny | 0.12 | 47.79 | 64.76 | 55.00 | 60.47 | 3277 / 3580 / 1783 |
| Boston | Fixed 2 m | Tiny | 0.13 | 42.81 | 54.80 | 48.07 | 51.90 | 2773 / 3704 / 2287 |
| Boston | Fixed 4 m | Tiny | 0.12 | 62.27 | 84.39 | 71.66 | 78.79 | 4270 / 2587 / 790 |
| Boston | Crown-aware | Small | 0.12 | 46.74 | 66.68 | 54.96 | 61.44 | 3374 / 3845 / 1686 |
| Boston | Fixed 2 m | Small | 0.12 | 40.99 | 58.48 | 48.20 | 53.88 | 2959 / 4260 / 2101 |
| Boston | Fixed 4 m | Small | 0.12 | 59.20 | 84.47 | 69.61 | 77.83 | 4274 / 2945 / 786 |
| NYC | Crown-aware | Tiny | 0.15 | 64.86 | 70.31 | 67.48 | 69.15 | 360 / 195 / 152 |
| NYC | Fixed 2 m | Tiny | 0.16 | 61.90 | 65.04 | 63.43 | 64.39 | 333 / 205 / 179 |
| NYC | Fixed 4 m | Tiny | 0.15 | 80.54 | 87.30 | 83.79 | 85.86 | 447 / 108 / 65 |
| NYC | Crown-aware | Small | 0.16 | 70.66 | 71.48 | 71.07 | 71.32 | 366 / 152 / 146 |
| NYC | Fixed 2 m | Small | 0.15 | 65.04 | 67.58 | 66.28 | 67.05 | 346 / 186 / 166 |
| NYC | Fixed 4 m | Small | 0.16 | 84.94 | 85.94 | 85.44 | 85.74 | 440 / 78 / 72 |

### Fixed confidence thresholds

All policies are reported at the same 0.13 and 0.35 confidence thresholds. Higher confidence has substantial recall cost; it does not remove the need to evaluate duplicates and missed groups.

| City | Policy | Model | Threshold | P % | R % | F1 % | F2 % |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SF | Crown-aware | Tiny | 0.13 | 72.25 | 78.53 | 75.26 | 77.19 |
| SF | Crown-aware | Tiny | 0.35 | 92.35 | 45.80 | 61.23 | 50.93 |
| SF | Fixed 2 m | Tiny | 0.13 | 71.04 | 77.21 | 74.00 | 75.89 |
| SF | Fixed 2 m | Tiny | 0.35 | 92.15 | 45.70 | 61.09 | 50.82 |
| SF | Fixed 4 m | Tiny | 0.13 | 79.42 | 86.31 | 82.72 | 84.84 |
| SF | Fixed 4 m | Tiny | 0.35 | 96.91 | 48.05 | 64.25 | 53.44 |
| SF | Crown-aware | Small | 0.13 | 73.27 | 77.20 | 75.18 | 76.38 |
| SF | Crown-aware | Small | 0.35 | 92.08 | 45.25 | 60.68 | 50.37 |
| SF | Fixed 2 m | Small | 0.13 | 72.01 | 75.86 | 73.89 | 75.06 |
| SF | Fixed 2 m | Small | 0.35 | 91.46 | 44.94 | 60.27 | 50.03 |
| SF | Fixed 4 m | Small | 0.13 | 81.16 | 85.50 | 83.27 | 84.60 |
| SF | Fixed 4 m | Small | 0.35 | 97.08 | 47.71 | 63.98 | 53.11 |
| Boston | Crown-aware | Tiny | 0.13 | 49.81 | 63.75 | 55.92 | 60.37 |
| Boston | Crown-aware | Tiny | 0.35 | 87.45 | 4.68 | 8.89 | 5.78 |
| Boston | Fixed 2 m | Tiny | 0.13 | 42.81 | 54.80 | 48.07 | 51.90 |
| Boston | Fixed 2 m | Tiny | 0.35 | 86.72 | 4.64 | 8.82 | 5.73 |
| Boston | Fixed 4 m | Tiny | 0.13 | 64.83 | 82.98 | 72.79 | 78.58 |
| Boston | Fixed 4 m | Tiny | 0.35 | 96.68 | 5.18 | 9.83 | 6.39 |
| Boston | Crown-aware | Small | 0.13 | 49.08 | 65.26 | 56.02 | 61.22 |
| Boston | Crown-aware | Small | 0.35 | 87.20 | 8.48 | 15.45 | 10.35 |
| Boston | Fixed 2 m | Small | 0.13 | 43.06 | 57.25 | 49.15 | 53.71 |
| Boston | Fixed 2 m | Small | 0.35 | 86.99 | 8.46 | 15.42 | 10.32 |
| Boston | Fixed 4 m | Small | 0.13 | 62.07 | 82.53 | 70.85 | 77.43 |
| Boston | Fixed 4 m | Small | 0.35 | 96.75 | 9.41 | 17.15 | 11.48 |
| NYC | Crown-aware | Tiny | 0.13 | 62.52 | 70.70 | 66.36 | 68.90 |
| NYC | Crown-aware | Tiny | 0.35 | 88.46 | 4.49 | 8.55 | 5.54 |
| NYC | Fixed 2 m | Tiny | 0.13 | 58.20 | 65.82 | 61.78 | 64.14 |
| NYC | Fixed 2 m | Tiny | 0.35 | 88.46 | 4.49 | 8.55 | 5.54 |
| NYC | Fixed 4 m | Tiny | 0.13 | 77.55 | 87.70 | 82.31 | 85.46 |
| NYC | Fixed 4 m | Tiny | 0.35 | 100.00 | 5.08 | 9.67 | 6.27 |
| NYC | Crown-aware | Small | 0.13 | 67.09 | 72.07 | 69.49 | 71.02 |
| NYC | Crown-aware | Small | 0.35 | 91.67 | 10.74 | 19.23 | 13.05 |
| NYC | Fixed 2 m | Small | 0.13 | 63.27 | 67.97 | 65.54 | 66.97 |
| NYC | Fixed 2 m | Small | 0.35 | 91.67 | 10.74 | 19.23 | 13.05 |
| NYC | Fixed 4 m | Small | 0.13 | 80.73 | 86.72 | 83.62 | 85.45 |
| NYC | Fixed 4 m | Small | 0.35 | 100.00 | 11.72 | 20.98 | 14.23 |

## Taxonomy, attributes and vegetation

Taxonomy accuracy is conditional on matched, eligible labelled detections, so each model/threshold can have a different denominator. Genus and species eligibility also differ; genus accuracy need not exceed species accuracy. These are not end-to-end species detection recall. The table uses crown-aware matching at its best-F2 threshold and at both fixed thresholds; attributes for the fixed-distance policies remain in the linked raw metrics.

| City | Model | Operating point | Genus n | Genus top-1 % | Species n | Species top-1 % | Species top-5 % | Species macro-F1 % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | Tiny | best 0.13 | 4028 | 47.69 | 3632 | 50.30 | 77.45 | 26.17 |
| SF | Small | best 0.12 | 3987 | 48.63 | 3595 | 51.91 | 75.86 | 26.36 |
| Boston | Tiny | best 0.12 | 1601 | 35.85 | 1142 | 41.33 | 68.30 | 13.67 |
| Boston | Small | best 0.12 | 1623 | 36.04 | 1158 | 36.44 | 67.10 | 10.96 |
| NYC | Tiny | best 0.15 | 266 | 14.29 | 233 | 15.88 | 41.63 | 8.29 |
| NYC | Small | best 0.16 | 262 | 12.60 | 228 | 14.47 | 47.37 | 5.38 |
| SF | Tiny | fixed 0.13 | 4028 | 47.69 | 3632 | 50.30 | 77.45 | 26.17 |
| SF | Tiny | fixed 0.35 | 2430 | 48.77 | 2212 | 52.35 | 80.02 | 25.76 |
| SF | Small | fixed 0.13 | 3968 | 48.77 | 3580 | 52.04 | 76.09 | 26.45 |
| SF | Small | fixed 0.35 | 2369 | 50.74 | 2156 | 53.48 | 79.04 | 25.43 |
| Boston | Tiny | fixed 0.13 | 1575 | 35.68 | 1126 | 41.03 | 68.03 | 13.67 |
| Boston | Tiny | fixed 0.35 | 121 | 25.62 | 90 | 26.67 | 66.67 | 17.60 |
| Boston | Small | fixed 0.13 | 1590 | 36.29 | 1145 | 36.33 | 67.16 | 10.63 |
| Boston | Small | fixed 0.35 | 194 | 27.32 | 144 | 27.08 | 55.56 | 16.40 |
| NYC | Tiny | fixed 0.13 | 268 | 14.55 | 235 | 16.17 | 41.70 | 8.18 |
| NYC | Tiny | fixed 0.35 | 15 | 0.00 | 12 | 0.00 | 16.67 | 0.00 |
| NYC | Small | fixed 0.13 | 263 | 12.55 | 229 | 14.41 | 47.60 | 5.36 |
| NYC | Small | fixed 0.35 | 31 | 6.45 | 22 | 0.00 | 22.73 | 0.00 |

At best crown-aware F2, Small’s species top-1 is higher in SF but lower in Boston and NYC. Top-5 is lower in SF and Boston and higher in NYC. NYC genus/species top-1 remains weak for both models. The change in matched subsets prevents attributing these differences solely to classification capacity.

| City | Model | Crown n | Radius MAE m | Radius bias m | DBH n | DBH MAE in | DBH bias in |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SF | Tiny | 1769 | 0.602 | -0.240 | 3284 | 4.326 | -2.176 |
| SF | Small | 1737 | 0.609 | -0.308 | 3252 | 4.283 | -1.548 |
| Boston | Tiny | 1939 | 1.258 | -0.990 | 1578 | 9.722 | -8.365 |
| Boston | Small | 2008 | 1.340 | -1.034 | 1617 | 9.356 | -7.825 |
| NYC | Tiny | 171 | 1.235 | -1.135 | 252 | 3.750 | -3.089 |
| NYC | Small | 183 | 1.087 | -0.988 | 246 | 3.387 | -2.761 |

Vegetation category is reported separately from scientific taxonomy. Frozen validation contains only one SF and three Boston shrub-mass targets, no NYC vegetation targets and no Copse targets. At best crown-aware thresholds each model matches one vegetation target in SF and one in Boston, with category accuracy 0% in both cases. The other two Boston vegetation targets are not matched at those operating points. This support is inadequate for a meaningful category-quality claim; vegetation is exempt from the single-tree occupancy objective.

| City | Model | Matched vegetation n | Category accuracy % |
| --- | --- | --- | --- |
| SF | Tiny | 1 | 0.00 |
| SF | Small | 1 | 0.00 |
| Boston | Tiny | 1 | 0.00 |
| Boston | Small | 1 | 0.00 |
| NYC | Tiny | 0 | — |
| NYC | Small | 0 | — |

## Explicit single-tree occupancy and duplicates

The independent full-group reconstruction reproduced labels and scoring masks before counting detections in retained-label ownership groups. Coverage is only 4 SF groups in 3 chips, 19 Boston groups in 7 chips and 2 NYC groups in 2 chips. These are per-chip materialized groups, not unique drawn regions. Occupancy precision = covered groups / detections in groups; occupancy recall = covered groups / groups. Excess peaks = sum(max(count−1, 0)). A detection anywhere in the ownership group counts, so this is an occupancy proxy, not distance-matched precision/recall or duplicate-classifier AP.

| City | Model | Threshold | Groups | Detections | Covered | Missed | Duplicate groups | Excess peaks | Occupancy P % | Occupancy R % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | Tiny | 0.13 | 4 | 5 | 4 | 0 | 1 | 1 | 80.00 | 100.00 |
| SF | Tiny | 0.35 | 4 | 1 | 1 | 3 | 0 | 0 | 100.00 | 25.00 |
| SF | Small | 0.13 | 4 | 7 | 4 | 0 | 3 | 3 | 57.14 | 100.00 |
| SF | Small | 0.35 | 4 | 1 | 1 | 3 | 0 | 0 | 100.00 | 25.00 |
| Boston | Tiny | 0.13 | 19 | 39 | 18 | 1 | 8 | 21 | 46.15 | 94.74 |
| Boston | Tiny | 0.35 | 19 | 0 | 0 | 19 | 0 | 0 | — | 0.00 |
| Boston | Small | 0.13 | 19 | 39 | 18 | 1 | 12 | 21 | 46.15 | 94.74 |
| Boston | Small | 0.35 | 19 | 0 | 0 | 19 | 0 | 0 | — | 0.00 |
| NYC | Tiny | 0.13 | 2 | 2 | 1 | 1 | 1 | 1 | 50.00 | 50.00 |
| NYC | Tiny | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | — | 0.00 |
| NYC | Small | 0.13 | 2 | 2 | 2 | 0 | 0 | 0 | 100.00 | 100.00 |
| NYC | Small | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | — | 0.00 |

At 0.13, Small has more duplicated SF groups (3 versus 1) and more duplicated Boston groups (12 versus 8), despite identical Boston total excess peaks (21). Small covers both NYC groups without excess peaks; Tiny covers one with one excess peak. At 0.35 both models miss every Boston and NYC group and three of four SF groups. Zero duplicates at that threshold therefore reflects missed detections rather than a satisfactory one-tree-per-group solution.

## Geographic uncertainty

We resampled 768 m projected geographic blocks, assigned by chip center in each frozen city CRS: 27 SF blocks, 24 Boston blocks and only 2 NYC blocks. Each of 5,000 bootstrap replicates uses identical sampled blocks for Tiny and Small, aggregates TP/FP/FN, then recomputes metrics. Per-block totals were independently required to equal the full frozen scorer for every model, policy and operating point. Seed 20261027 plus city index; percentile 95% intervals. Values below are percentage-point changes, Small minus Tiny.

| City | Crown-aware operating point | Blocks | ΔP [95% CI] | ΔR [95% CI] | ΔF1 [95% CI] | ΔF2 [95% CI] |
| --- | --- | --- | --- | --- | --- | --- |
| SF | .13 | 27 | +1.02 [+0.17, +1.75] | -1.33 [-2.15, -0.50] | -0.08 [-0.79, +0.58] | -0.81 [-1.55, -0.09] |
| SF | .35 | 27 | -0.28 [-1.45, +0.75] | -0.55 [-1.83, +1.17] | -0.55 [-1.77, +1.01] | -0.56 [-1.85, +1.16] |
| SF | validation_best | 27 | -0.16 [-1.04, +0.54] | -0.93 [-1.69, -0.17] | -0.51 [-1.22, +0.08] | -0.75 [-1.46, -0.10] |
| Boston | .13 | 24 | -0.73 [-2.64, +0.84] | +1.50 [+0.29, +3.33] | +0.10 [-1.13, +1.64] | +0.85 [-0.26, +2.43] |
| Boston | .35 | 24 | -0.26 [-3.80, +2.63] | +3.79 [+2.62, +6.09] | +6.56 [+4.68, +10.06] | +4.57 [+3.17, +7.23] |
| Boston | validation_best | 24 | -1.05 [-2.78, +0.42] | +1.92 [+0.78, +3.28] | -0.04 [-1.22, +1.28] | +0.97 [-0.06, +2.21] |
| NYC | .13 | 2 | +4.57; CI withheld | +1.37; CI withheld | +3.13; CI withheld | +2.12; CI withheld |
| NYC | .35 | 2 | +3.21; CI withheld | +6.25; CI withheld | +10.68; CI withheld | +7.50; CI withheld |
| NYC | validation_best | 2 | +5.79; CI withheld | +1.17; CI withheld | +3.59; CI withheld | +2.17; CI withheld |

The complete uncertainty JSON includes fixed-2 m and fixed-4 m intervals as well. Best-threshold intervals hold the validation-selected thresholds fixed and exclude threshold-selection uncertainty. Dependence can extend beyond 768 m; only one grid scale was analyzed. No multiple-comparison adjustment or uncertainty across training seeds is included. NYC intervals are withheld. These are descriptive results on curated validation, not independent generalization estimates; tests remain sealed.

## Exposure, compute and convergence

Both runs selected step 5,660 (20 source-pass equivalents) and stopped at step 9,057 (~32.0035 source-pass equivalents), with 12 non-improving checks after the best checkpoint. Both stopped by `early_stopping`, before the wall-clock cutoff and before the 80-pass cap. Neither result was wall-clock truncated. Each retained top three checkpoints plus the true last checkpoint, and the selection receipt, training result and independently CPU-loaded last-checkpoint callbacks agreed.

| Measure | Tiny | Small |
| --- | --- | --- |
| Initialization validation loss | 11.165029 | 11.336502 |
| Best validation loss | 3.562596 | 3.539204 |
| Final validation loss | 3.918488 | 3.914175 |
| Training interval, seconds | 12364.975 | 15682.067 |
| Seconds to best after baseline | 7802.334 | 9897.043 |
| Seconds per optimizer step | 1.365240 | 1.731486 |
| Optimizer steps per second | 0.732472 | 0.577539 |
| Supervisor elapsed, seconds | 16632.939 | 19695.535 |
| Allocation to job finish, seconds | 16981.653 | 20018.443 |
| Sampled GPU memory, MiB | 2361 | 3193 |

The measured training interval begins at full initialization-validation completion and ends at final validation; it includes training, validation and checkpoint overhead. Tiny: 01:09:38.868–04:35:43.844 EDT (3.435 h); Small: 01:05:26.062–05:26:48.129 EDT (4.356 h). Small started about four minutes earlier after faster preparation. Tiny delivered 1.2683× the step throughput; Small needed 26.8% longer for the same optimizer-step exposure. Memory figures are monitoring samples, not measured peaks. Both used one A10 in us-east-1.

The allocation-to-job-finish durations (4.7171 h Tiny; 5.5607 h Small) omit subsequent archive transfer and teardown. Exact provider billing and exact termination timestamps are unavailable; these values must not be reported as billed hours or an exact invoice. All recorded GPU job work and teardown finished within the eight-hour hard limits. Offline CPU scoring occurred later and is outside GPU lifetime. Equal wall-clock budgets do not imply equal exposure; this pair happened to reach the same exposure through early stopping.

Late validation total loss rose in both runs. Last-five-check genus/species losses also rose; Tiny DBH/crown losses still improved slightly, while Small’s crown trend increased. This is consistent with diminishing or negative validation returns under this recipe, not proof that optimization converged or that either architecture cannot improve with another authorized recipe. Short 3–5-check windows oscillate and components have different scales.

OLS slopes below are changes in recorded validation loss per 1,000 optimizer steps, per source pass (283 optimizer steps), and per wall-clock hour. Positive means loss increased. These are raw logged component slopes; objective weights should not be inferred from their magnitudes.

| Model | Last checks | Component | Slope / 1,000 steps | Slope / source pass | Slope / hour |
| --- | --- | --- | --- | --- | --- |
| Tiny | 3 | loss | +0.384565 | +0.108832 | +1.035500 |
| Tiny | 3 | center_loss | +0.164956 | +0.046683 | +0.444137 |
| Tiny | 3 | single_tree_peak_loss | +0.001803 | +0.000510 | +0.004854 |
| Tiny | 3 | dbh_loss | -0.017193 | -0.004866 | -0.046297 |
| Tiny | 3 | crown_loss | -0.002227 | -0.000630 | -0.005991 |
| Tiny | 3 | genus_loss | +0.232429 | +0.065777 | +0.625863 |
| Tiny | 3 | species_loss | +0.224832 | +0.063627 | +0.605447 |
| Tiny | 5 | loss | +0.029593 | +0.008375 | +0.079217 |
| Tiny | 5 | center_loss | -0.004872 | -0.001379 | -0.013281 |
| Tiny | 5 | single_tree_peak_loss | +0.000553 | +0.000156 | +0.001485 |
| Tiny | 5 | dbh_loss | -0.006251 | -0.001769 | -0.016793 |
| Tiny | 5 | crown_loss | -0.001254 | -0.000355 | -0.003369 |
| Tiny | 5 | genus_loss | +0.035910 | +0.010163 | +0.096363 |
| Tiny | 5 | species_loss | +0.040676 | +0.011511 | +0.109203 |
| Small | 3 | loss | +0.078704 | +0.022273 | +0.168048 |
| Small | 3 | center_loss | -0.133867 | -0.037884 | -0.285433 |
| Small | 3 | single_tree_peak_loss | -0.000116 | -0.000033 | -0.000248 |
| Small | 3 | dbh_loss | -0.002723 | -0.000771 | -0.005814 |
| Small | 3 | crown_loss | +0.002210 | +0.000625 | +0.004708 |
| Small | 3 | genus_loss | +0.242627 | +0.068664 | +0.517609 |
| Small | 3 | species_loss | +0.181056 | +0.051239 | +0.386256 |
| Small | 5 | loss | +0.130279 | +0.036869 | +0.277447 |
| Small | 5 | center_loss | -0.002173 | -0.000615 | -0.004441 |
| Small | 5 | single_tree_peak_loss | -0.000426 | -0.000121 | -0.000907 |
| Small | 5 | dbh_loss | -0.002000 | -0.000566 | -0.004258 |
| Small | 5 | crown_loss | +0.000401 | +0.000113 | +0.000850 |
| Small | 5 | genus_loss | +0.135307 | +0.038292 | +0.287939 |
| Small | 5 | species_loss | +0.131645 | +0.037255 | +0.280203 |

## Completion, teardown and provenance

Tiny supervisor finished at 04:36:18 EDT and Small at 05:27:27 EDT on 2026-10-27, both exit 0. Both collectors exited 0 with “Verified results downloaded; teardown acknowledged”: remote/local archive SHA comparison and download ACK succeeded. COMPLETE markers and all three validation inference cohorts were independently verified. Exact instance IDs were reconfirmed terminated with null IPs during final reporting; no recovery instance, redeployment or extension was used.

| Model | Exact instance ID | API state | Best step / last step |
| --- | --- | --- | --- |
| Tiny | c618faeb4a8b4b508248c7ac98ea8e88 | TERMINATED / IP null | 5,660 / 9,057 |
| Small | feca69e89a87438d9433bf81ec9163e2 | TERMINATED / IP null | 5,660 / 9,057 |

Selected checkpoint file for both: `010-0005660.ckpt`. Checkpoint and archive hashes:

| Model | Selected checkpoint SHA-256 | Collected archive SHA-256 |
| --- | --- | --- |
| Tiny | 6dc8605d1663f2a37148f8998ecd1c2d524ed9ccf81b82afb741cbeed3d5bc45 | ae3bc66d585099a5083a163add71ccd67315ce122456857986ab3e57333f6f6a |
| Small | 26b8909ab9735d90d9965441935d8f558c6cb9197d35fd35b0270798b3f44d14 | 2bff78fa2a01ae04db009deee89367f6da47399c2f84c1be5da3caa1e5d88f3f |

Shared source archive SHA-256: `2796aef6112ed2c1a10eaa84e73e80f13365abd7ba3858166fcb9c0f4d0a1b16`. Tiny input archive: `e7578adebf7454474097415201f8e8832dc81ed9b2c26b8be93996eae993916b`; Small input archive: `a67a78065c18d923169b0dd49695e4e79a476aee67ef0404ee305beac97dadb4`. CPU reproduction image: `sha256:7056b34ad23ba0029533189ce2836aed51aa489558e99bd8d2404fd91baccf1a`. Original frozen inputs and all scoring-provenance file hashes were rechecked when generating this report.

Current parent remains `sf-boston-nyc-naip-swin-30cm-v17-curated-finetune`, checkpoint `000-0000282.ckpt`, SHA-256 `77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df`. Historical v17 full-validation and earlier more-done cohort metrics are preserved and are not compared numerically here because their frozen cohorts differ. This architecture experiment is explicitly ineligible for automatic parent advancement. No curator restart, model-default switch or live annotation mutation was performed.

Evidence and machine-readable results:

- [sf-boston-nyc-naip-30cm-imagenet-capacity-v1-pair.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-naip-30cm-imagenet-capacity-v1-pair.json>)
- [sf-boston-nyc-naip-30cm-imagenet-capacity-v1-tiny-completion-audit.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-naip-30cm-imagenet-capacity-v1-tiny-completion-audit.json>)
- [sf-boston-nyc-naip-30cm-imagenet-capacity-v1-small-completion-audit.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-naip-30cm-imagenet-capacity-v1-small-completion-audit.json>)
- [best.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/frozen-comparison/best.json>)
- [fixed.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/frozen-comparison/fixed.json>)
- [sweep.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/frozen-comparison/sweep.json>)
- [provenance.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/frozen-comparison/provenance.json>)
- [both-models-mask-audit.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/both-models-mask-audit.json>)
- [metrics.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/occupancy/metrics.json>)
- [provenance.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/occupancy/provenance.json>)
- [geographic-uncertainty.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/output/geographic-uncertainty.json>)
- [training-analysis.json](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/training-analysis.json>)
- [paired_uncertainty.py](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/paired_uncertainty.py>)
- [run_cpu_audit.py](<C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/tiny-small-imagenet-linux-reproduction/run_cpu_audit.py>)
