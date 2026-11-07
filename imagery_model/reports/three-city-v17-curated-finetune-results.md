# Three-city v17 baseline-guarded fine-tune results

Completed October 26, 2026. Primary scoring, supplemental occupancy and native goal checks verified; GPU termination independently verified.

**V17 wins the aggregate loss gate, with small and mixed detection changes.** Best crown-aware F2 changes from 65.622% to 65.563% in SF (−0.059 percentage points), 56.640% to 56.789% in Boston (+0.149 pp), and 58.129% to 59.155% in NYC (+1.026 pp). These compare fresh v15 and v17 predictions using the same peak decoder and v17 frozen supervision.

At common confidence 0.13, pooled F1 improves by 0.621 pp and F2 by only 0.036 pp: false positives fall by 746, while true positives fall by 94. This is a precision/recall tradeoff, not a uniform improvement. SF recall and high-confidence performance regress. NYC scientific-species accuracy regresses slightly at its best-F2 point despite improved detection and genus accuracy.

Selected checkpoint: `sf-boston-nyc-naip-swin-30cm-v17-curated-finetune/checkpoints/000-0000282.ckpt`, SHA256 `77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df`. `selected=trained`, `architecture_adapted=false`, `eligible_for_parent_advance=true`. This verified checkpoint is eligible as the next-training parent under the saved loss gate. No curator restart, reviewer model/confidence default switch, or live annotation changes are part of this result.

## Training and baseline

| Validation step | Validation loss | Result |
| --- | --- | --- |
| 0 | 4.144672393798828 | Parent baseline |
| 282 | 4.138549804687500 | Selected / best trained |
| 566 | 4.172544956207275 | No improvement |
| 848 | 4.177591323852539 | No improvement |
| 1132 | 4.176295280456543 | No improvement |
| 1414 | 4.174236297607422 | Fourth non-improving check; stop |

The full step-zero baseline covered all 1,223 frozen validation windows before optimizer steps. Absolute loss improvement is 0.0061225891, or 0.147722%. True final `last.ckpt` is step 1415 / epoch 3; its early-stopping state has wait count 4 and best loss 4.1385498046875. It is retained separately from the selected step-282 checkpoint.

Starting parent is original v15 `001-0000566.ckpt`, SHA256 `ed82b3f210777810cc441626d00e14c6aa9a5dadaf2de07582baa53b31a308c9`. The new objective halves genus and species to 0.5 each; center 1, DBH 0.5, crown 1 and explicit-single-tree extra peaks 1 remain. Compare baseline and trained loss within this run only; historical losses used different objectives.

Fresh optimizer LR 3e-5, batch 2 / accumulation 16, bf16, Swin Tiny / 99 classes, 512 pixels at 0.3 m. Fixed-pair augmentation: 9,051 source windows / 18,102 presentations, 283 steps per source pass, validation every 4,526 batches, patience 4 and maximum 80 source passes. Latest curation, revised weights and optimization are confounded; this is not a causal weights-only experiment.

## Matched evaluation and confidence selection

The original v15 parent was rerun inside v17 using the same deterministic peak-tie decoder and frozen inputs. The comparison uses `parent-validation`, `parent-validation-usbos` and `parent-validation-usnyc`, validated against `paired-parent-validation.json` and the original parent hash. Historical cached v15 detections are not used. Historical headline scores and thresholds therefore cannot isolate a model gain: they also differ in decoding and supervision.

| City | Train chips | Validation chips | Frozen validation targets | Vegetation targets |
| --- | --- | --- | --- | --- |
| SF | 4468 | 567 | 12456 | 1 |
| Boston | 4577 | 637 | 10989 | 3 |
| NYC | 6 | 19 | 641 | 0 |

NYC admits only six completed curated training chips / 677 targets. Its 19 validation windows / 641 targets have identical pinned v8 IDs and pixels, with rebuilt frozen labels/masks; 93 test windows remain sealed. No unreviewed NYC or recombinant inputs were admitted. Protected SF source refresh preserved validation/test imagery.

Crown-aware matching uses half the target crown radius, clipped to 2–4 m; unknown radius uses 2 m. Matching is one-to-one and confidence ordered. Unmatched zero-mask detections are ignored. Confidence sweep: 0.01–0.95 in 0.01 steps, higher confidence breaks equal-F2 ties. These are validation-selected operating points, not held-out estimates.

**Best crown-F2 confidence, fresh v15 → v17: SF 0.13 → 0.12; Boston 0.12 → 0.12; NYC 0.11 → 0.09.** These suggest operating points for v17 if explicitly selected in the reviewer. NYC at 0.13 is almost as good: F2 59.116% versus 59.155% at 0.09, with higher precision (58.462% versus 53.729%). The sweep maximum is not evidence that a small threshold change is robust.

## Best-F2 operating points

| City | Model | Matching | Confidence | P % | R % | F1 % | F2 % | TP | FP | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | v15 | Crown-aware | 0.13 | 64.76 | 65.84 | 65.30 | 65.62 | 8201 | 4462 | 4255 |
| SF | v15 | 2 m | 0.13 | 63.84 | 64.90 | 64.37 | 64.69 | 8084 | 4579 | 4372 |
| SF | v15 | 4 m | 0.13 | 76.43 | 77.70 | 77.06 | 77.44 | 9678 | 2985 | 2778 |
| SF | v17 | Crown-aware | 0.12 | 65.04 | 65.70 | 65.36 | 65.56 | 8183 | 4399 | 4273 |
| SF | v17 | 2 m | 0.12 | 64.12 | 64.77 | 64.45 | 64.64 | 8068 | 4514 | 4388 |
| SF | v17 | 4 m | 0.11 | 75.20 | 77.93 | 76.54 | 77.37 | 9707 | 3202 | 2749 |
| Boston | v15 | Crown-aware | 0.12 | 46.61 | 59.86 | 52.41 | 56.64 | 6578 | 7535 | 4411 |
| Boston | v15 | 2 m | 0.12 | 43.06 | 55.30 | 48.42 | 52.33 | 6077 | 8036 | 4912 |
| Boston | v15 | 4 m | 0.11 | 61.53 | 82.85 | 70.62 | 77.48 | 9104 | 5691 | 1885 |
| Boston | v17 | Crown-aware | 0.12 | 47.88 | 59.56 | 53.08 | 56.79 | 6545 | 7125 | 4444 |
| Boston | v17 | 2 m | 0.12 | 44.21 | 55.00 | 49.02 | 52.44 | 6044 | 7626 | 4945 |
| Boston | v17 | 4 m | 0.11 | 63.40 | 82.23 | 71.59 | 77.62 | 9036 | 5217 | 1953 |
| NYC | v15 | Crown-aware | 0.11 | 54.45 | 59.13 | 56.69 | 58.13 | 379 | 317 | 262 |
| NYC | v15 | 2 m | 0.11 | 50.86 | 55.23 | 52.95 | 54.29 | 354 | 342 | 287 |
| NYC | v15 | 4 m | 0.10 | 73.05 | 83.31 | 77.84 | 81.03 | 534 | 197 | 107 |
| NYC | v17 | Crown-aware | 0.09 | 53.73 | 60.69 | 57.00 | 59.15 | 389 | 335 | 252 |
| NYC | v17 | 2 m | 0.09 | 51.52 | 58.19 | 54.65 | 56.72 | 373 | 351 | 268 |
| NYC | v17 | 4 m | 0.09 | 73.90 | 83.46 | 78.39 | 81.36 | 535 | 189 | 106 |

Attributes are conditional on eligible localized matches. Genus and scientific species have different support; matched examples also change between models and thresholds. Unknown/default trees contribute to localization scoring but do not become scientific-species errors without an eligible taxonomy target. Vegetation category accuracy is separate.

| City | Model | Matching | Confidence | Genus % | Genus n | Species % | Top-5 % | Species n | Vegetation % | Vegetation n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | v15 | Crown-aware | 0.13 | 48.60 | 6728 | 53.04 | 77.59 | 5943 | 0.00 | 1 |
| SF | v15 | 2 m | 0.13 | 48.32 | 6649 | 52.80 | 77.53 | 5866 | 0.00 | 1 |
| SF | v15 | 4 m | 0.13 | 48.28 | 8001 | 52.40 | 76.77 | 7048 | 0.00 | 1 |
| SF | v17 | Crown-aware | 0.12 | 48.93 | 6718 | 53.51 | 77.28 | 5930 | 0.00 | 1 |
| SF | v17 | 2 m | 0.12 | 48.64 | 6639 | 53.29 | 77.23 | 5855 | 0.00 | 1 |
| SF | v17 | 4 m | 0.11 | 48.23 | 8009 | 52.47 | 76.41 | 7054 | 0.00 | 1 |
| Boston | v15 | Crown-aware | 0.12 | 42.71 | 4554 | 43.24 | 70.45 | 3675 | 0.00 | 2 |
| Boston | v15 | 2 m | 0.12 | 41.40 | 4244 | 41.74 | 69.53 | 3440 | 0.00 | 2 |
| Boston | v15 | 4 m | 0.11 | 44.00 | 6487 | 44.19 | 70.34 | 5300 | 0.00 | 2 |
| Boston | v17 | Crown-aware | 0.12 | 43.21 | 4524 | 44.05 | 70.31 | 3671 | 0.00 | 2 |
| Boston | v17 | 2 m | 0.12 | 41.85 | 4205 | 42.56 | 69.32 | 3416 | 0.00 | 2 |
| Boston | v17 | 4 m | 0.11 | 44.14 | 6427 | 44.79 | 69.67 | 5262 | 0.00 | 2 |
| NYC | v15 | Crown-aware | 0.11 | 17.04 | 311 | 16.73 | 50.18 | 275 | — | 0 |
| NYC | v15 | 2 m | 0.11 | 16.67 | 294 | 16.67 | 50.00 | 258 | — | 0 |
| NYC | v15 | 4 m | 0.10 | 17.52 | 451 | 17.30 | 51.91 | 393 | — | 0 |
| NYC | v17 | Crown-aware | 0.09 | 18.50 | 319 | 16.37 | 49.11 | 281 | — | 0 |
| NYC | v17 | 2 m | 0.09 | 18.83 | 308 | 17.04 | 49.63 | 270 | — | 0 |
| NYC | v17 | 4 m | 0.09 | 18.00 | 450 | 16.15 | 49.49 | 390 | — | 0 |

## Fixed confidence 0.13 and 0.35

| City | Model | Matching | Confidence | P % | R % | F1 % | F2 % | TP | FP | FN |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | v15 | Crown-aware | 0.13 | 64.76 | 65.84 | 65.30 | 65.62 | 8201 | 4462 | 4255 |
| SF | v15 | Crown-aware | 0.35 | 86.22 | 39.33 | 54.02 | 44.13 | 4899 | 783 | 7557 |
| SF | v15 | 2 m | 0.13 | 63.84 | 64.90 | 64.37 | 64.69 | 8084 | 4579 | 4372 |
| SF | v15 | 2 m | 0.35 | 85.74 | 39.11 | 53.72 | 43.89 | 4872 | 810 | 7584 |
| SF | v15 | 4 m | 0.13 | 76.43 | 77.70 | 77.06 | 77.44 | 9678 | 2985 | 2778 |
| SF | v15 | 4 m | 0.35 | 95.93 | 43.76 | 60.11 | 49.10 | 5451 | 231 | 7005 |
| SF | v17 | Crown-aware | 0.13 | 66.43 | 65.25 | 65.84 | 65.49 | 8128 | 4107 | 4328 |
| SF | v17 | Crown-aware | 0.35 | 86.46 | 37.78 | 52.58 | 42.58 | 4706 | 737 | 7750 |
| SF | v17 | 2 m | 0.13 | 65.49 | 64.33 | 64.91 | 64.56 | 8013 | 4222 | 4443 |
| SF | v17 | 2 m | 0.35 | 85.93 | 37.55 | 52.26 | 42.31 | 4677 | 766 | 7779 |
| SF | v17 | 4 m | 0.13 | 78.25 | 76.86 | 77.55 | 77.14 | 9574 | 2661 | 2882 |
| SF | v17 | 4 m | 0.35 | 96.16 | 42.02 | 58.48 | 47.35 | 5234 | 209 | 7222 |
| Boston | v15 | Crown-aware | 0.13 | 48.45 | 58.91 | 53.17 | 56.47 | 6474 | 6889 | 4515 |
| Boston | v15 | Crown-aware | 0.35 | 78.75 | 13.25 | 22.68 | 15.89 | 1456 | 393 | 9533 |
| Boston | v15 | 2 m | 0.13 | 44.79 | 54.46 | 49.15 | 52.21 | 5985 | 7378 | 5004 |
| Boston | v15 | 2 m | 0.35 | 78.31 | 13.18 | 22.56 | 15.81 | 1448 | 401 | 9541 |
| Boston | v15 | 4 m | 0.13 | 66.31 | 80.64 | 72.77 | 77.30 | 8861 | 4502 | 2128 |
| Boston | v15 | 4 m | 0.35 | 95.57 | 16.08 | 27.53 | 19.29 | 1767 | 82 | 9222 |
| Boston | v17 | Crown-aware | 0.13 | 49.77 | 58.66 | 53.85 | 56.64 | 6446 | 6506 | 4543 |
| Boston | v17 | Crown-aware | 0.35 | 78.74 | 14.02 | 23.81 | 16.78 | 1541 | 416 | 9448 |
| Boston | v17 | 2 m | 0.13 | 45.97 | 54.18 | 49.74 | 52.31 | 5954 | 6998 | 5035 |
| Boston | v17 | 2 m | 0.35 | 78.23 | 13.93 | 23.65 | 16.67 | 1531 | 426 | 9458 |
| Boston | v17 | 4 m | 0.13 | 67.92 | 80.05 | 73.49 | 77.29 | 8797 | 4155 | 2192 |
| Boston | v17 | 4 m | 0.35 | 95.96 | 17.09 | 29.01 | 20.45 | 1878 | 79 | 9111 |
| NYC | v15 | Crown-aware | 0.13 | 57.30 | 58.19 | 57.74 | 58.01 | 373 | 278 | 268 |
| NYC | v15 | Crown-aware | 0.35 | 43.75 | 1.09 | 2.13 | 1.36 | 7 | 9 | 634 |
| NYC | v15 | 2 m | 0.13 | 53.61 | 54.45 | 54.02 | 54.28 | 349 | 302 | 292 |
| NYC | v15 | 2 m | 0.35 | 43.75 | 1.09 | 2.13 | 1.36 | 7 | 9 | 634 |
| NYC | v15 | 4 m | 0.13 | 79.26 | 80.50 | 79.88 | 80.25 | 516 | 135 | 125 |
| NYC | v15 | 4 m | 0.35 | 93.75 | 2.34 | 4.57 | 2.91 | 15 | 1 | 626 |
| NYC | v17 | Crown-aware | 0.13 | 58.46 | 59.28 | 58.87 | 59.12 | 380 | 270 | 261 |
| NYC | v17 | Crown-aware | 0.35 | 38.89 | 1.09 | 2.12 | 1.36 | 7 | 11 | 634 |
| NYC | v17 | 2 m | 0.13 | 56.00 | 56.79 | 56.39 | 56.63 | 364 | 286 | 277 |
| NYC | v17 | 2 m | 0.35 | 38.89 | 1.09 | 2.12 | 1.36 | 7 | 11 | 634 |
| NYC | v17 | 4 m | 0.13 | 79.23 | 80.34 | 79.78 | 80.12 | 515 | 135 | 126 |
| NYC | v17 | 4 m | 0.35 | 94.44 | 2.65 | 5.16 | 3.29 | 17 | 1 | 624 |

Attributes are conditional on eligible localized matches. Genus and scientific species have different support; matched examples also change between models and thresholds. Unknown/default trees contribute to localization scoring but do not become scientific-species errors without an eligible taxonomy target. Vegetation category accuracy is separate.

| City | Model | Matching | Confidence | Genus % | Genus n | Species % | Top-5 % | Species n | Vegetation % | Vegetation n |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | v15 | Crown-aware | 0.13 | 48.60 | 6728 | 53.04 | 77.59 | 5943 | 0.00 | 1 |
| SF | v15 | Crown-aware | 0.35 | 50.82 | 4195 | 56.32 | 81.14 | 3716 | 0.00 | 1 |
| SF | v15 | 2 m | 0.13 | 48.32 | 6649 | 52.80 | 77.53 | 5866 | 0.00 | 1 |
| SF | v15 | 2 m | 0.35 | 50.83 | 4175 | 56.32 | 81.23 | 3697 | 0.00 | 1 |
| SF | v15 | 4 m | 0.13 | 48.28 | 8001 | 52.40 | 76.77 | 7048 | 0.00 | 1 |
| SF | v15 | 4 m | 0.35 | 50.40 | 4698 | 55.76 | 80.76 | 4164 | 0.00 | 1 |
| SF | v17 | Crown-aware | 0.13 | 49.13 | 6672 | 53.72 | 77.49 | 5894 | 0.00 | 1 |
| SF | v17 | Crown-aware | 0.35 | 51.56 | 4036 | 57.56 | 81.67 | 3579 | — | 0 |
| SF | v17 | 2 m | 0.13 | 48.84 | 6593 | 53.50 | 77.44 | 5819 | 0.00 | 1 |
| SF | v17 | 2 m | 0.35 | 51.57 | 4016 | 57.60 | 81.77 | 3561 | — | 0 |
| SF | v17 | 4 m | 0.13 | 48.60 | 7906 | 52.87 | 76.83 | 6970 | 0.00 | 1 |
| SF | v17 | 4 m | 0.35 | 51.18 | 4515 | 57.04 | 81.40 | 4006 | — | 0 |
| Boston | v15 | Crown-aware | 0.13 | 42.75 | 4489 | 43.33 | 70.53 | 3628 | 0.00 | 1 |
| Boston | v15 | Crown-aware | 0.35 | 35.82 | 1058 | 34.28 | 67.08 | 881 | — | 0 |
| Boston | v15 | 2 m | 0.13 | 41.48 | 4185 | 41.84 | 69.64 | 3396 | 0.00 | 1 |
| Boston | v15 | 2 m | 0.35 | 35.62 | 1050 | 34.10 | 66.93 | 874 | — | 0 |
| Boston | v15 | 4 m | 0.13 | 44.37 | 6322 | 44.59 | 70.82 | 5174 | 0.00 | 1 |
| Boston | v15 | 4 m | 0.35 | 38.12 | 1322 | 35.35 | 68.08 | 1109 | — | 0 |
| Boston | v17 | Crown-aware | 0.13 | 43.41 | 4457 | 44.33 | 70.41 | 3616 | 0.00 | 1 |
| Boston | v17 | Crown-aware | 0.35 | 38.53 | 1129 | 35.46 | 67.57 | 956 | — | 0 |
| Boston | v17 | 2 m | 0.13 | 42.04 | 4144 | 42.79 | 69.39 | 3365 | 0.00 | 1 |
| Boston | v17 | 2 m | 0.35 | 38.39 | 1120 | 35.34 | 67.51 | 948 | — | 0 |
| Boston | v17 | 4 m | 0.13 | 44.59 | 6257 | 45.38 | 70.21 | 5123 | 0.00 | 1 |
| Boston | v17 | 4 m | 0.35 | 39.48 | 1421 | 35.61 | 67.68 | 1213 | — | 0 |
| NYC | v15 | Crown-aware | 0.13 | 17.21 | 308 | 16.91 | 50.37 | 272 | — | 0 |
| NYC | v15 | Crown-aware | 0.35 | 16.67 | 6 | 0.00 | 33.33 | 6 | — | 0 |
| NYC | v15 | 2 m | 0.13 | 16.84 | 291 | 16.86 | 50.20 | 255 | — | 0 |
| NYC | v15 | 2 m | 0.35 | 16.67 | 6 | 0.00 | 33.33 | 6 | — | 0 |
| NYC | v15 | 4 m | 0.13 | 17.54 | 439 | 17.28 | 52.36 | 382 | — | 0 |
| NYC | v15 | 4 m | 0.35 | 7.14 | 14 | 0.00 | 33.33 | 12 | — | 0 |
| NYC | v17 | Crown-aware | 0.13 | 18.91 | 312 | 16.67 | 49.28 | 276 | — | 0 |
| NYC | v17 | Crown-aware | 0.35 | 40.00 | 5 | 20.00 | 60.00 | 5 | — | 0 |
| NYC | v17 | 2 m | 0.13 | 19.27 | 301 | 17.36 | 49.81 | 265 | — | 0 |
| NYC | v17 | 2 m | 0.35 | 40.00 | 5 | 20.00 | 60.00 | 5 | — | 0 |
| NYC | v17 | 4 m | 0.13 | 18.39 | 435 | 16.67 | 50.00 | 378 | — | 0 |
| NYC | v17 | 4 m | 0.35 | 13.33 | 15 | 8.33 | 41.67 | 12 | — | 0 |

## Pooled crown-aware counts at common confidence

| Model | Confidence | TP | FP | FN | P % | R % | F1 % | F2 % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v15 | 0.13 | 15048 | 11629 | 9038 | 56.41 | 62.48 | 59.29 | 61.16 |
| v17 | 0.13 | 14954 | 10883 | 9132 | 57.88 | 62.09 | 59.91 | 61.20 |
| v15 | 0.35 | 6362 | 1185 | 17724 | 84.30 | 26.41 | 40.22 | 30.62 |
| v17 | 0.35 | 6254 | 1164 | 17832 | 84.31 | 25.97 | 39.70 | 30.14 |

At 0.35 pooled F2 declines by 0.482 pp. SF loses 193 true positives at this threshold; Boston gains 85 and NYC is unchanged. Use the fixed-threshold rows to distinguish operating-point shifts from best-threshold comparisons.

## Taxonomy and vegetation caveats

At best crown-F2 points, SF genus/species rise 48.603→48.928% / 53.037→53.508%; Boston rises 42.710→43.214% / 43.238→44.048%. NYC genus rises 17.042→18.495%, but scientific species falls 16.727→16.370%. At common 0.13 NYC species also falls 16.912→16.667%. All metrics are conditional and do not establish improved classification on an identical matched-tree subset.

Top-5 scientific-species accuracy declines at the best crown-F2 points in all three cities: SF 77.587→77.285%, Boston 70.449→70.308%, NYC 50.182→49.110%. These regressions remain relevant despite the detection priority and the improved SF/Boston top-1 accuracy.

Only four validation vegetation targets exist (one SF, three Boston); matched vegetation accuracy at the best crown-F2 points is 0% where supported, and NYC has no support. This does not establish useful vegetation classification.

## Explicit single-tree coverage and duplication

Coverage was scanned independently on every materialized training and validation NPZ before GPU termination. Counts below are per-chip retained-center ownership groups, not unique drawn polygons. The extra-peak loss applies only to explicit single-tree regions; vegetation groups are exempt.

| City | Train groups | Train chips with groups | Validation groups | Validation chips with groups |
| --- | --- | --- | --- | --- |
| SF | 24 | 19 | 2 | 1 |
| Boston | 198 | 102 | 19 | 7 |
| NYC | 13 | 3 | 1 | 1 |

The supplemental rebuild independently reproduces the primary labels/masks and all validation group counts. One allowed detection per ownership group defines occupancy precision/recall; these are proxies, not distance-matched detection P/R or duplicate-classifier AP. No detections in a group is a miss, not successful duplicate removal.

At confidence 0.13, pooled excess peaks decline **31→27**, but the number of groups containing duplicates stays **17→17**. Covered groups decline **22→21** because v17 misses one of the two SF groups. Boston supplies the clearest small improvement: excess peaks **28→25**, with all 19 groups still covered; NYC is unchanged at one excess peak. Pooled occupancy precision improves **41.51→43.75%**, while occupancy recall declines **100→95.45%**. The sample is only 22 ownership groups across nine validation chips. At 0.35 both models miss all 22 groups, so their zero duplicates do not demonstrate success.

| City | Model | Confidence | Groups | Detections | Covered | Missed | Duplicate groups | Excess peaks | Occupancy P % | Occupancy R % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SF | v15 | 0.13 | 2 | 4 | 2 | 0 | 1 | 2 | 50.00 | 100.00 |
| SF | v15 | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | — | 0.00 |
| SF | v17 | 0.13 | 2 | 2 | 1 | 1 | 1 | 1 | 50.00 | 50.00 |
| SF | v17 | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | — | 0.00 |
| Boston | v15 | 0.13 | 19 | 47 | 19 | 0 | 15 | 28 | 40.43 | 100.00 |
| Boston | v15 | 0.35 | 19 | 0 | 0 | 19 | 0 | 0 | — | 0.00 |
| Boston | v17 | 0.13 | 19 | 44 | 19 | 0 | 15 | 25 | 43.18 | 100.00 |
| Boston | v17 | 0.35 | 19 | 0 | 0 | 19 | 0 | 0 | — | 0.00 |
| NYC | v15 | 0.13 | 1 | 2 | 1 | 0 | 1 | 1 | 50.00 | 100.00 |
| NYC | v15 | 0.35 | 1 | 0 | 0 | 1 | 0 | 0 | — | 0.00 |
| NYC | v17 | 0.13 | 1 | 2 | 1 | 0 | 1 | 1 | 50.00 | 100.00 |
| NYC | v17 | 0.35 | 1 | 0 | 0 | 1 | 0 | 0 | — | 0.00 |

Pooled occupancy totals:

| Model | Confidence | Groups | Detections | Covered | Missed | Duplicate groups | Excess peaks | Occupancy P % | Occupancy R % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v15 | 0.13 | 22 | 53 | 22 | 0 | 17 | 31 | 41.51 | 100.00 |
| v17 | 0.13 | 22 | 48 | 21 | 1 | 17 | 27 | 43.75 | 95.45 |
| v15 | 0.35 | 22 | 0 | 0 | 22 | 0 | 0 | — | 0.00 |
| v17 | 0.35 | 22 | 0 | 0 | 22 | 0 | 0 | — | 0.00 |

## Reproducibility and boundary diagnosis

Initial Windows reconstruction stopped at one Boston saved prediction location: chip `r000046_c000012`, output `(226,0)`, native mask 1 versus local 0. A confirmed-background polygon intended at the tile edge reprojected to y=1.862645149e-9 pixels locally. The original strict polygon boundary test is sensitive to platform roundoff. A tested future geometry fix includes numerical boundary distance/bounds tolerance, but using that fix changed four SF locations relative to the historical masks and correctly failed the audit again.

**Final historical scoring uses original archived code under local CPU Linux, not the new geometry fix.** Locked Python 3.13.12, NumPy 2.5.2, Pandas 3.0.5, pyproj 3.7.2 / PROJ 9.5.1, rasterio 1.5.1 / GDAL 3.12.4, Ubuntu 24.04, uv 0.10.10 and Rust 1.91.1 reproduce all three cities’ frozen labels and scoring masks under the unchanged strict checks. The required Rust extension built/imported with no fallback. Collected artifacts are mounted read-only; VRT overlays only rewrite host paths. No mask values, finite guards, tolerances in the reconstruction assertion, or frozen inputs were overridden.

The one-chip Linux probe needed an all-null genus/species column cast (None/object versus NaN/float) to match full-cohort types. This changed no labels or masks. The full scorer then passed with its original assertions unchanged. Both failed Windows benchmarks and the diagnostic receipt are preserved. The future boundary fix is not evidence that historical training masks were changed.

## Completion, integrity and artifacts

Deployment, persistent storage and frozen upload hashes passed. Actual A10/CUDA focal and crown backward guards passed, as did all three 512px batch-2 probes: synthetic 99-class bf16, synthetic fp32 and original-v15 bf16, with zero bad/missing gradients. Full parent baseline, all three configs, NYC admission and all six train/validation cohorts were audited. Collector exited 0 after verifying the archive and acknowledging download. Supervisor exited 0; run COMPLETE exists. Exact Lambda instance `3dac6c81b040458894557879b7365810` was API-verified terminated with null IP. No recovery allocation was used.

| Prediction cohort | Windows | Prediction rows |
| --- | --- | --- |
| SF selected validation | 567 | 263787 |
| SF paired parent validation | 567 | 246477 |
| SF train | 4468 | 2024670 |
| Boston selected validation | 637 | 275781 |
| Boston paired parent validation | 637 | 263390 |
| Boston train | 4577 | 1957214 |
| NYC selected validation | 19 | 9201 |
| NYC paired parent validation | 19 | 9114 |
| NYC train | 6 | 2785 |

All nine cohort IDs and per-chip prediction counts matched their frozen manifests. Primary COMPLETE and all 33 source provenance hashes were independently rechecked after remapping Linux paths locally. Supplemental group-array/file hashes and native scoring provenance also passed. Native SF/Boston `crown_center_score` at confidence 0.35 agrees with every corresponding fixed-table TP/FP/FN/P/R/F1/F2 value; its generated boilerplate is not the interpretation of this experiment.

| Artifact | SHA256 |
| --- | --- |
| Frozen source | 5232594100b7688a7322cf2a01b7c4ae8ac6379470275d9a963b652876ab3210 |
| Frozen inputs | ef9ed39de696d88bf763a7f702d7445e7ef36ce2eb70ecbdb27ed013933b11ef |
| Collected archive | 72bee73b8458e6951b01cf90b79af8071ab0496e5f741ae9931b6cf553c64706 |
| Selected checkpoint | 77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df |
| Parent baseline serialization | 6a59b023eda1f1c8441f8d72b49657f8c554b7637069cc27669c13dcb7197dd3 |
| Validation input signature | 38bf357c1bc09332105ead0417c2c79f20cddbe058d6b5c80cd949087b923812 |

- [Primary benchmark](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v17-linux-reproduction/output/frozen-comparison)
- [Occupancy audit](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v17-linux-reproduction/output/occupancy)
- [Native goal scoring](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v17-linux-reproduction/output/native-goal)
- [Linux source/environment receipt](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v17-linux-reproduction/reproduction-environment.json)
- [Boundary diagnosis](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v17-mask-diagnostic/diagnosis.json)

Lineage advancement follows the verified aggregate-loss policy. It does not assert improvement on every metric and does not switch the live reviewer.
