# Four-city v20 results and explicit promotion

Current next-training parent: **sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune, 001-0000569.ckpt**, SHA256 `ab428f589573be7666f840d467f672ad3144b50a948a8b621cc6eb0ceec86c9d`. The user explicitly chose this best-trained checkpoint after reviewing Logan improvement and the aggregate validation regression. This supersedes automatic parent retention for lineage only. Original training-result.json and immutable selected-parent outputs remain unchanged; user-promotion.json binds the chosen weights to the original result hash. Future automatic selection still uses the parent-baseline gate.

The trained candidate reduced trusted-background false positives on the same 24 Logan training tiles from159 to7 (95.6%) at confidence.13, using paired CPU float32 and the same archived decoder. Aggregate validation loss increased from3.451700926 to3.484341145 (+0.946%). Full four-city P/R/F1/F2 for the promoted candidate have not yet been computed. The retained-parent detection tables below must NOT be attributed to the promoted candidate. No city publication or live curator/default change occurred.

## Training, admission and completion

Tiny RGBN,99classes,512px at30cm; batch2/accumulation16,bf16,LR3e-5,fresh optimizer, fixed_pair_v1/source_passes_v1,patience12,max80sourcepasses. Loss weights center1,DBH.5,crown1,genus.5,species.5,explicit-single-tree1; vegetation ownership exemption unchanged. Started from verified v19 step1701. Frozen published human curation admits partial and negative-only train tiles with trusted supervision only. Validation requires full-tile more_done. Spatial splits and sealed tests unchanged; no temporal imagery in this run.

| Inference cohort | Tiles | Targets | Predictions |
| --- | --- | --- | --- |
| validation | 220 | 7000 | 93695 |
| parent-validation | 220 | 7000 | 93695 |
| train-ussfo | 4468 | 142140 | 1963889 |
| validation-usbos | 89 | 5113 | 36911 |
| parent-validation-usbos | 89 | 5113 | 36911 |
| train-usbos | 4625 | 101896 | 1909130 |
| validation-usnyc | 10 | 574 | 4413 |
| parent-validation-usnyc | 10 | 574 | 4413 |
| train-usnyc | 7 | 732 | 3173 |
| validation-usffx | 2 | 61 | 1024 |
| parent-validation-usffx | 2 | 61 | 1024 |
| train-usffx | 1 | 6 | 512 |

Normal early_stopping at3414 optimizer steps, patience12/wait12; best-trained569, true last3414, top3 retained. Original selection chose parent-baseline.ckpt. Callback/result/checkpoint agreement and all12 cohort coverage verified. All321 validation windows included in full step-zero baseline. Fairfax contributes1train tile/6targets and2validation/61targets; all24Logan negative training tiles have nonempty masks. 9101sourcewindows,18202pairedpresentations,285optimizersteps per source-pass budget.

| TensorBoard step | Loss | Time UTC |
| --- | --- | --- |
| 0 | 3.4517009258270264 | 2026-11-12T14:31:03.175352+00:00 |
| 284 | 3.5170488357543945 | 2026-11-12T14:37:18.655416+00:00 |
| 569 | 3.4843411445617676 | 2026-11-12T14:43:29.364810+00:00 |
| 853 | 3.532381772994995 | 2026-11-12T14:49:36.869611+00:00 |
| 1138 | 3.521296501159668 | 2026-11-12T14:55:45.394271+00:00 |
| 1422 | 3.551236867904663 | 2026-11-12T15:01:52.683111+00:00 |
| 1707 | 3.509763717651367 | 2026-11-12T15:07:57.472378+00:00 |
| 1991 | 3.5623018741607666 | 2026-11-12T15:14:05.987852+00:00 |
| 2276 | 3.526771068572998 | 2026-11-12T15:20:10.413494+00:00 |
| 2560 | 3.5418639183044434 | 2026-11-12T15:26:14.281010+00:00 |
| 2845 | 3.5830416679382324 | 2026-11-12T15:32:18.632855+00:00 |
| 3129 | 3.57413649559021 | 2026-11-12T15:38:22.880441+00:00 |
| 3414 | 3.6118361949920654 | 2026-11-12T15:44:27.226047+00:00 |

## Loss components and convergence

| Component | Baseline | Best trained | Delta | Last |
| --- | --- | --- | --- | --- |
| validation/loss | 3.4517009258270264 | 3.4843411445617676 | 0.03264021873474121 | 3.6118361949920654 |
| validation/center_loss | 1.2512000799179077 | 1.2801544666290283 | 0.028954386711120605 | 1.3095132112503052 |
| validation/single_tree_peak_loss | 0.0022397059947252274 | 0.002437219023704529 | 0.00019751302897930145 | 0.0018058775458484888 |
| validation/dbh_loss | 0.10597904026508331 | 0.10786398500204086 | 0.00188494473695755 | 0.10234049707651138 |
| validation/crown_loss | 0.01417568139731884 | 0.014340328983962536 | 0.00016464758664369583 | 0.013527076691389084 |
| validation/genus_loss | 2.1349477767944336 | 2.135648727416992 | 0.0007009506225585938 | 2.2287089824676514 |
| validation/species_loss | 2.1272449493408203 | 2.1313047409057617 | 0.004059791564941406 | 2.242931842803955 |

Center loss accounts for approximately89% of the best-trained aggregate regression. TensorBoard steps can differ by one from saved optimizer steps. Step/source-pass is the configured budget ratio. Neither declining train loss nor early stopping proves convergence or lack of capacity.

| Component | Last3/1k steps | Last3/pass | Last3/hour | Last5/1k steps | Last5/pass | Last5/hour |
| --- | --- | --- | --- | --- | --- | --- |
| validation/loss | 0.05065342634352496 | 0.014436226507904614 | 0.14228491529525625 | 0.07115509710663542 | 0.020279202675391097 | 0.2000586944907712 |
| validation/center_loss | 0.047270252459142986 | 0.01347202195085575 | 0.13286321125857117 | 0.028089743563241863 | 0.008005576915523931 | 0.07897709939463683 |
| validation/single_tree_peak_loss | -0.00047122759189776063 | -0.00013429986369086177 | -0.0013240689035721243 | -4.9711636602324e-05 | -1.4167816431662341e-05 | -0.00013954096343936882 |
| validation/dbh_loss | -0.004138956292987769 | -0.001179602543501514 | -0.011604575536979824 | 0.0008942629773527337 | 0.0002548649485455291 | 0.00252073887866019 |
| validation/crown_loss | -0.000990236460642532 | -0.0002822173912831216 | -0.0027755609398207337 | 0.000566296448852735 | 0.00016139448792302946 | 0.0015939784710242428 |
| validation/genus_loss | 0.007005738398852373 | 0.001996635443672926 | 0.019600947696140358 | 0.04479614749606123 | 0.01276690203637745 | 0.12594548935555566 |
| validation/species_loss | 0.006823169549377985 | 0.001944603321572726 | 0.01904818842297276 | 0.03940826702662708 | 0.011231356102588718 | 0.1107912925965242 |

## Paired Logan training diagnostic

| Model | Threshold | Raw peaks | Trusted-background FP | Affected tiles | Outside supervision |
| --- | --- | --- | --- | --- | --- |
| parent | 0.13 | 421 | 159 | 13 | 262 |
| parent | 0.35 | 0 | 0 | 0 | 0 |
| best_trained | 0.13 | 43 | 7 | 5 | 36 |
| best_trained | 0.35 | 0 | 0 | 0 | 0 |

All24tiles have zero tree targets. Peaks outside trusted supervision are unscored. Stored GPU-bf16 retained-parent outputs had160trusted/426raw at.13; CPU parent has159/421. Do not mix those runtimes in the paired improvement ratio. Mask reconstruction was exact at all original GPU peak locations. Only the best-trained checkpoint was evaluated here, not every saved checkpoint. This demonstrates learning on curated training negatives, not generalization; zero peaks at.35 does not establish acceptable positive-tree recall.

## Original retained-parent validation metrics (NOT promoted candidate)

Selected-parent v20 outputs and fresh paired v19 outputs have exactly equal predictions, labels and masks. Crown matching uses half truth crown radius bounded2-4m; fixed2m/4m are separate. Best thresholds maximize F2 on validation.

Best F2

| City | Policy | Threshold | P% | R% | F1% | F2% |
| --- | --- | --- | --- | --- | --- | --- |
| ussfo | crown-half-radius-2-4m | 0.13 | 72.630 | 78.929 | 75.649 | 77.583 |
| ussfo | fixed-2m | 0.13 | 71.447 | 77.643 | 74.416 | 76.319 |
| ussfo | fixed-4m | 0.13 | 79.755 | 86.671 | 83.070 | 85.194 |
| usbos | crown-half-radius-2-4m | 0.12 | 50.667 | 67.612 | 57.926 | 63.373 |
| usbos | fixed-2m | 0.13 | 46.322 | 58.498 | 51.703 | 55.576 |
| usbos | fixed-4m | 0.12 | 63.491 | 84.725 | 72.587 | 79.413 |
| usnyc | crown-half-radius-2-4m | 0.12 | 69.219 | 78.746 | 73.676 | 76.636 |
| usnyc | fixed-2m | 0.12 | 65.697 | 74.739 | 69.927 | 72.737 |
| usnyc | fixed-4m | 0.12 | 78.714 | 89.547 | 83.782 | 87.148 |
| usffx | crown-half-radius-2-4m | 0.04 | 33.898 | 65.574 | 44.693 | 55.249 |
| usffx | fixed-2m | 0.07 | 36.842 | 57.377 | 44.872 | 51.622 |
| usffx | fixed-4m | 0.07 | 47.368 | 73.770 | 57.692 | 66.372 |

Fixed thresholds

| City | Policy | Threshold | P% | R% | F1% | F2% |
| --- | --- | --- | --- | --- | --- | --- |
| ussfo | crown-half-radius-2-4m | 0.13 | 72.630 | 78.929 | 75.649 | 77.583 |
| ussfo | crown-half-radius-2-4m | 0.35 | 92.814 | 50.000 | 64.989 | 55.082 |
| ussfo | fixed-2m | 0.13 | 71.447 | 77.643 | 74.416 | 76.319 |
| ussfo | fixed-2m | 0.35 | 92.230 | 49.686 | 64.581 | 54.735 |
| ussfo | fixed-4m | 0.13 | 79.755 | 86.671 | 83.070 | 85.194 |
| ussfo | fixed-4m | 0.35 | 97.269 | 52.400 | 68.109 | 57.726 |
| usbos | crown-half-radius-2-4m | 0.13 | 52.733 | 66.595 | 58.859 | 63.269 |
| usbos | crown-half-radius-2-4m | 0.35 | 84.718 | 11.168 | 19.734 | 13.514 |
| usbos | fixed-2m | 0.13 | 46.322 | 58.498 | 51.703 | 55.576 |
| usbos | fixed-2m | 0.35 | 83.383 | 10.992 | 19.423 | 13.301 |
| usbos | fixed-4m | 0.13 | 66.052 | 83.415 | 73.725 | 79.249 |
| usbos | fixed-4m | 0.35 | 95.401 | 12.576 | 22.222 | 15.218 |
| usnyc | crown-half-radius-2-4m | 0.13 | 71.589 | 77.700 | 74.520 | 76.396 |
| usnyc | crown-half-radius-2-4m | 0.35 | 86.957 | 3.484 | 6.700 | 4.312 |
| usnyc | fixed-2m | 0.13 | 68.058 | 73.868 | 70.844 | 72.628 |
| usnyc | fixed-2m | 0.35 | 86.957 | 3.484 | 6.700 | 4.312 |
| usnyc | fixed-4m | 0.13 | 81.380 | 88.328 | 84.712 | 86.845 |
| usnyc | fixed-4m | 0.35 | 95.652 | 3.833 | 7.370 | 4.743 |
| usffx | crown-half-radius-2-4m | 0.13 | 46.377 | 52.459 | 49.231 | 51.118 |
| usffx | crown-half-radius-2-4m | 0.35 | 100.000 | 13.115 | 23.188 | 15.873 |
| usffx | fixed-2m | 0.13 | 44.928 | 50.820 | 47.692 | 49.521 |
| usffx | fixed-2m | 0.35 | 100.000 | 13.115 | 23.188 | 15.873 |
| usffx | fixed-4m | 0.13 | 52.174 | 59.016 | 55.385 | 57.508 |
| usffx | fixed-4m | 0.35 | 100.000 | 13.115 | 23.188 | 15.873 |

Classification is conditional on distance-matched detections; vegetation is separate. These are retained-parent scores.

| City | Point | Threshold | Genus n | Genus% | Species n | Species% | Vegetation n | Vegetation% | Top5% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | best | 0.13 | 4110 | 51.971 | 3722 | 55.884 | 1 | 0.000 | 78.748 |
| usbos | best | 0.12 | 1682 | 39.536 | 1223 | 46.034 | 1 | 0.000 | 69.092 |
| usnyc | best | 0.12 | 305 | 20.000 | 266 | 21.429 | 0 | n/a | 49.624 |
| usffx | best | 0.04 | 5 | 20.000 | 5 | 0.000 | 0 | n/a | 20.000 |
| ussfo | fixed | 0.13 | 4110 | 51.971 | 3722 | 55.884 | 1 | 0.000 | 78.748 |
| ussfo | fixed | 0.35 | 2727 | 53.099 | 2483 | 57.551 | 0 | n/a | 81.031 |
| usbos | fixed | 0.13 | 1659 | 39.482 | 1206 | 45.854 | 1 | 0.000 | 69.154 |
| usbos | fixed | 0.35 | 316 | 28.797 | 233 | 29.185 | 0 | n/a | 53.648 |
| usnyc | fixed | 0.13 | 303 | 20.132 | 264 | 21.212 | 0 | n/a | 49.242 |
| usnyc | fixed | 0.35 | 11 | 27.273 | 9 | 22.222 | 0 | n/a | 44.444 |
| usffx | fixed | 0.13 | 3 | 33.333 | 3 | 0.000 | 0 | n/a | 0.000 |
| usffx | fixed | 0.35 | 0 | n/a | 0 | n/a | 0 | n/a | n/a |

## Retained-parent ownership and uncertainty

Explicit single-tree occupancy is a region proxy separate from distance-matched P/R. Materialized groups may be clipped portions of drawn regions.

| City | Validation chips | Group chips | Groups |
| --- | --- | --- | --- |
| ussfo | 220 | 3 | 4 |
| usbos | 89 | 8 | 20 |
| usnyc | 10 | 2 | 2 |
| usffx | 2 | 0 | 0 |

| City | Threshold | Groups | Peaks | Covered | Missed | Duplicate groups | Excess | Occupancy P% | Occupancy R% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | 0.13 | 4 | 8 | 4 | 0 | 3 | 4 | 50.000 | 100.000 |
| ussfo | 0.35 | 4 | 0 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| usbos | 0.13 | 20 | 50 | 20 | 0 | 16 | 30 | 40.000 | 100.000 |
| usbos | 0.35 | 20 | 0 | 0 | 20 | 0 | 0 | n/a | 0.000 |
| usnyc | 0.13 | 2 | 6 | 2 | 0 | 2 | 4 | 33.333 | 100.000 |
| usnyc | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | n/a | 0.000 |
| usffx | 0.13 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | 0.35 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |

Paired5000-resample768m block bootstrap reproduces scorer aggregate counts first. Retained-parent differences are exactly zero, not evidence for promoted candidate accuracy. Intervals withheld below10blocks; best-threshold intervals conditional on selected thresholds. Dependence beyond blocks and selection uncertainty not captured.

| City | Blocks |
| --- | --- |
| usbos | 25 |
| usffx | 1 |
| usnyc | 2 |
| ussfo | 27 |

## Compute and reproducibility

One A10 allocated at2026-11-12T13:11:49.615294+00:00. Supervisor elapsed8465.973s; allocation through job finish9485.891s. Baseline completion to last validation4404.051s. Final validation before16:11:49UTC cutoff; job exit0 at2026-11-12T15:49:55.505836+00:00. Exact instance9be2782296a04fcdbfe72443bdaf0ad3 APIterminated/IPnull; collector exit0 with verified archiveSHA/downloadACK. No recovery or extension. Provider billing/termination timestamp, peakGPUmemory and exact FLOPs unavailable.

Archive SHA3fe1f491c3a666862964ee5c34e37f897a41da7eb437ff6a3cacca5e53156145. Source SHA55767c2a09e09d85df3948cef8706a85b0500481358ff77e4db8c39fe47963d3; inputs SHA983be811229a852a8450a64acfc27d3d7ac43f792c7b373a1ffd5eeae1ae9ef8. All GPU backward/Rust/focal/crown, storage, upload, timer and Docker cutoff audits passed. Original archived source and strict label/mask guards used locally; eight mask tables exact, maximumdifference0.0. Own Linux CPU containers2CPU/4GiB. Effective collected normalizations used after historical path missing locally; values exact. Fairfax original full-cohort build projected to two validation tiles. Four VRT overlays change paths only. Failed initial audit attempts preserved; frozen-comparison-v2 is completed authoritative output.

## Receipts

- [sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune-completion-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune-completion-audit.json)
- [provenance.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/frozen-comparison-v2/provenance.json)
- [best.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/frozen-comparison-v2/best.json)
- [fixed.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/frozen-comparison-v2/fixed.json)
- [both-models-mask-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/both-models-mask-audit.json)
- [metrics.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/occupancy/metrics.json)
- [geographic-uncertainty.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/geographic-uncertainty.json)
- [summary.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v20-linux-reproduction/output/logan-candidate-v2/summary.json)
