# Four-city, multi-year v22 background-weighted fine-tune results

Automatic selection chose **trained**, validation loss **3.581860304** against same-cohort v21 baseline **3.583425999**. Best trained loss was **3.581860304**. Stop reason: **wall_clock_training_cutoff**, true last optimizer step **3626**. Eligible for next-training-parent advancement: **True**. Live curator model defaults and annotations were not changed.

Selected checkpoint: **000-0000573.ckpt**, SHA256 `e5f458e808f8cd45c20f615b5527e7ee107f4f7eab01f37cef2ff7585d49fa57`, run `sf-boston-nyc-fairfax-naip-swin-30cm-v22-curated-finetune`.

The comparison parent is the verified v21 checkpoint 002-0001146.ckpt, SHA256 1393e64ef61d145648f3cf792e0c7fac6a391c9efd4760c75042b6170457a3c6. It was freshly inferred on this run’s frozen validation inputs using the same decoder. Historical validation scores on different frozen labels or objectives are not used as this comparison.

At the common 0.13 threshold, crown-aware detection changes are:

| City/acquisition | v21 F2% | v22 F2% | Delta F2 pp | Delta TP | Delta FP |
| --- | --- | --- | --- | --- | --- |
| ussfo | 77.012 | 77.005 | -0.008 | -1 | -2 |
| usbos | 63.311 | 62.816 | -0.496 | -36 | -39 |
| usnyc | 75.900 | 78.151 | 2.251 | 23 | 5 |
| usffx | 48.989 | 52.591 | 3.602 | 6 | 7 |
| usbos-naip-2025 | 51.935 | 53.269 | 1.335 | 32 | 100 |

City keys: ussfo=SF 2022, usbos=Boston 2023, usnyc=NYC 2023, usffx=Fairfax 2023. Boston 2025 is a separate acquisition. Positive delta FP means more false positives. The comparison has city-specific gains and regressions; uncertainty and classification support are detailed below.

The direct confirmed-background check at the same 0.13 threshold is mixed: Boston 2023 drops 107 to 93 tree peaks (13.1%) and NYC 35 to 28 (20%); Boston 2025 changes 29 to 28, while SF rises 85 to 90 and Fairfax 28 to 32. These count only explicit trusted empty areas on the same frozen validation cohort. Overall Boston 2023 recall drops 0.704 percentage points, so its background improvement comes with a detection tradeoff. There is no matched 1x fine-tuning control to isolate the effect of doubling background loss.

| City/acquisition | v21 background tree peaks | v22 background tree peaks | Delta |
| --- | --- | --- | --- |
| ussfo | 85 | 90 | 5 |
| usbos | 107 | 93 | -14 |
| usnyc | 35 | 28 | -7 |
| usffx | 28 | 32 | 4 |
| usbos-naip-2025 | 29 | 28 | -1 |

On the original 24 Logan training tiles, all with zero curated tree targets, the selected v22 has four tree peaks in trusted supervision at 0.13, spread over three tiles. Fifteen of those 24 tiles have explicit confirmed-background masks, and all four peaks also lie in those masks; the nine other tiles are not counted as explicit-mask coverage. This is selected-model training performance, not a paired reduction or generalization result. Boston 2021 has seven background tree peaks across its 24 training tiles at 0.13; it has no admitted validation cohort.

## Admission and training recipe

SwinTiny RGBN, 99 classes, 512 px at 30 cm; weights-only initialization with a fresh optimizer, LR 3e-5, batch 2 / accumulation 16, bf16, fixed_pair_v1/source_passes_v1, patience 12, maximum 80 source passes. Loss coefficients: center 1, DBH 0.5, crown 1, genus 0.5, species 0.5, explicit single-tree 1. Published curation admits partial and tree-free training tiles with trusted supervision only. New-year tiles require published human curation; uncurated temporal tiles are excluded. Validation requires explicit full-tile More done. No sealed test data were evaluated.

| City/acquisition | Train tiles | Train targets | Validation tiles | Validation targets |
| --- | --- | --- | --- | --- |
| ussfo | 4468 | 142140 | 220 | 7000 |
| usbos | 4624 | 101965 | 89 | 5113 |
| usnyc | 7 | 731 | 19 | 817 |
| usffx | 9 | 257 | 5 | 122 |
| usbos-naip-2021 | 24 | 0 | 0 | 0 |
| usbos-naip-2025 | 30 | 505 | 10 | 1279 |

Boston 2021 contributes 24 curated background training tiles and no validation tiles. Boston 2025 contributes 30 training and 10 validation tiles. Fairfax contributes 9 training and 5 validation tiles. SF 2020/SF 2024 have no eligible published curation and are excluded. The six curation snapshots are the sealed v22 publications. Explicit confirmed-background negative center focal terms use coefficient 2; all other loss coefficients are unchanged. The parent baseline is re-evaluated under this same new objective.

The full step-zero baseline covered 343 validation windows. Selected checkpoint, best trained checkpoint, true last state, callbacks, and training result agree. The three best trained checkpoints and true last are retained.

| Inference cohort | Tiles | Targets | Predictions |
| --- | --- | --- | --- |
| train-ussfo | 4468 | 142140 | 1929551 |
| validation | 220 | 7000 | 91759 |
| parent-validation | 220 | 7000 | 95411 |
| train-usbos | 4624 | 101965 | 1862681 |
| validation-usbos | 89 | 5113 | 35900 |
| parent-validation-usbos | 89 | 5113 | 36821 |
| train-usnyc | 7 | 731 | 3156 |
| validation-usnyc | 19 | 817 | 9186 |
| parent-validation-usnyc | 19 | 817 | 9040 |
| train-usffx | 9 | 257 | 4452 |
| validation-usffx | 5 | 122 | 2560 |
| parent-validation-usffx | 5 | 122 | 2560 |
| train-usbos-naip-2021 | 24 | 0 | 12288 |
| train-usbos-naip-2025 | 30 | 505 | 15360 |
| validation-usbos-naip-2025 | 10 | 1279 | 5120 |
| parent-validation-usbos-naip-2025 | 10 | 1279 | 5120 |

Prediction counts in this coverage table are retained peak candidates, up to 512 per tile, before applying the reported confidence threshold. They are not accepted-tree or false-positive counts.

## Validation detection comparison

All percentages below use each model’s predictions against identical frozen labels and strict rebuilt masks. Crown-aware matching uses half the truth crown size bounded 2–4 m; fixed 2 m/4 m are separate policies. Best thresholds maximize F2 on validation and are descriptive rather than an unbiased held-out estimate.

### Best-F2 operating points

| City/acquisition | Model | Policy | Threshold | P% | R% | F1% | F2% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v22 selected | crown-half-radius-2-4m | 0.14 | 72.472 | 78.229 | 75.240 | 77.005 |
| ussfo | v22 selected | fixed-2m | 0.13 | 70.217 | 77.229 | 73.556 | 75.716 |
| ussfo | v22 selected | fixed-4m | 0.13 | 78.750 | 86.614 | 82.495 | 84.918 |
| ussfo | v21 parent | crown-half-radius-2-4m | 0.13 | 71.397 | 78.557 | 74.806 | 77.012 |
| ussfo | v21 parent | fixed-2m | 0.13 | 70.267 | 77.314 | 73.623 | 75.794 |
| ussfo | v21 parent | fixed-4m | 0.14 | 79.844 | 86.300 | 82.947 | 84.927 |
| usbos | v22 selected | crown-half-radius-2-4m | 0.12 | 51.404 | 66.595 | 58.022 | 62.879 |
| usbos | v22 selected | fixed-2m | 0.13 | 46.794 | 57.520 | 51.606 | 54.999 |
| usbos | v22 selected | fixed-4m | 0.12 | 64.825 | 83.982 | 73.170 | 79.295 |
| usbos | v21 parent | crown-half-radius-2-4m | 0.13 | 53.381 | 66.399 | 59.182 | 63.311 |
| usbos | v21 parent | fixed-2m | 0.13 | 46.855 | 58.283 | 51.948 | 55.572 |
| usbos | v21 parent | fixed-4m | 0.13 | 66.714 | 82.985 | 73.965 | 79.125 |
| usnyc | v22 selected | crown-half-radius-2-4m | 0.14 | 74.885 | 79.559 | 77.151 | 78.578 |
| usnyc | v22 selected | fixed-2m | 0.14 | 71.198 | 75.643 | 73.353 | 74.710 |
| usnyc | v22 selected | fixed-4m | 0.12 | 78.846 | 90.330 | 84.199 | 87.774 |
| usnyc | v21 parent | crown-half-radius-2-4m | 0.12 | 70.764 | 78.213 | 74.302 | 76.600 |
| usnyc | v21 parent | fixed-2m | 0.12 | 68.217 | 75.398 | 71.628 | 73.843 |
| usnyc | v21 parent | fixed-4m | 0.12 | 80.509 | 88.984 | 84.535 | 87.149 |
| usffx | v22 selected | crown-half-radius-2-4m | 0.13 | 41.071 | 56.557 | 47.586 | 52.591 |
| usffx | v22 selected | fixed-2m | 0.14 | 41.772 | 54.098 | 47.143 | 51.084 |
| usffx | v22 selected | fixed-4m | 0.13 | 52.976 | 72.951 | 61.379 | 67.835 |
| usffx | v21 parent | crown-half-radius-2-4m | 0.14 | 43.151 | 51.639 | 47.015 | 49.685 |
| usffx | v21 parent | fixed-2m | 0.14 | 43.151 | 51.639 | 47.015 | 49.685 |
| usffx | v21 parent | fixed-4m | 0.12 | 51.462 | 72.131 | 60.068 | 66.768 |
| usbos-naip-2025 | v22 selected | crown-half-radius-2-4m | 0.1 | 35.930 | 64.191 | 46.072 | 55.465 |
| usbos-naip-2025 | v22 selected | fixed-2m | 0.1 | 29.934 | 53.479 | 38.384 | 46.210 |
| usbos-naip-2025 | v22 selected | fixed-4m | 0.1 | 48.359 | 86.396 | 62.009 | 74.652 |
| usbos-naip-2025 | v21 parent | crown-half-radius-2-4m | 0.1 | 36.693 | 60.907 | 45.797 | 53.806 |
| usbos-naip-2025 | v21 parent | fixed-2m | 0.08 | 27.931 | 53.088 | 36.604 | 44.985 |
| usbos-naip-2025 | v21 parent | fixed-4m | 0.1 | 50.306 | 83.503 | 62.787 | 73.767 |

### Fixed operating points

| City/acquisition | Model | Policy | Threshold | P% | R% | F1% | F2% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v22 selected | crown-half-radius-2-4m | 0.13 | 71.412 | 78.543 | 74.808 | 77.005 |
| ussfo | v22 selected | crown-half-radius-2-4m | 0.35 | 92.250 | 49.143 | 64.125 | 54.209 |
| ussfo | v22 selected | fixed-2m | 0.13 | 70.217 | 77.229 | 73.556 | 75.716 |
| ussfo | v22 selected | fixed-2m | 0.35 | 91.821 | 48.914 | 63.827 | 53.957 |
| ussfo | v22 selected | fixed-4m | 0.13 | 78.750 | 86.614 | 82.495 | 84.918 |
| ussfo | v22 selected | fixed-4m | 0.35 | 97.104 | 51.729 | 67.499 | 57.061 |
| ussfo | v21 parent | crown-half-radius-2-4m | 0.13 | 71.397 | 78.557 | 74.806 | 77.012 |
| ussfo | v21 parent | crown-half-radius-2-4m | 0.35 | 92.500 | 48.629 | 63.745 | 53.725 |
| ussfo | v21 parent | fixed-2m | 0.13 | 70.267 | 77.314 | 73.623 | 75.794 |
| ussfo | v21 parent | fixed-2m | 0.35 | 91.957 | 48.343 | 63.371 | 53.409 |
| ussfo | v21 parent | fixed-4m | 0.13 | 78.720 | 86.614 | 82.479 | 84.911 |
| ussfo | v21 parent | fixed-4m | 0.35 | 97.201 | 51.100 | 66.985 | 56.455 |
| usbos | v22 selected | crown-half-radius-2-4m | 0.13 | 53.445 | 65.695 | 58.940 | 62.816 |
| usbos | v22 selected | crown-half-radius-2-4m | 0.35 | 84.388 | 12.263 | 21.414 | 14.791 |
| usbos | v22 selected | fixed-2m | 0.13 | 46.794 | 57.520 | 51.606 | 54.999 |
| usbos | v22 selected | fixed-2m | 0.35 | 83.042 | 12.067 | 21.072 | 14.555 |
| usbos | v22 selected | fixed-4m | 0.13 | 67.399 | 82.848 | 74.329 | 79.216 |
| usbos | v22 selected | fixed-4m | 0.35 | 95.020 | 13.808 | 24.112 | 16.655 |
| usbos | v21 parent | crown-half-radius-2-4m | 0.13 | 53.381 | 66.399 | 59.182 | 63.311 |
| usbos | v21 parent | crown-half-radius-2-4m | 0.35 | 85.050 | 10.014 | 17.918 | 12.159 |
| usbos | v21 parent | fixed-2m | 0.13 | 46.855 | 58.283 | 51.948 | 55.572 |
| usbos | v21 parent | fixed-2m | 0.35 | 83.887 | 9.877 | 17.673 | 11.993 |
| usbos | v21 parent | fixed-4m | 0.13 | 66.714 | 82.985 | 73.965 | 79.125 |
| usbos | v21 parent | fixed-4m | 0.35 | 95.017 | 11.187 | 20.017 | 13.584 |
| usnyc | v22 selected | crown-half-radius-2-4m | 0.13 | 72.575 | 79.682 | 75.963 | 78.151 |
| usnyc | v22 selected | crown-half-radius-2-4m | 0.35 | 95.890 | 8.568 | 15.730 | 10.476 |
| usnyc | v22 selected | fixed-2m | 0.13 | 69.008 | 75.765 | 72.229 | 74.310 |
| usnyc | v22 selected | fixed-2m | 0.35 | 94.521 | 8.446 | 15.506 | 10.326 |
| usnyc | v22 selected | fixed-4m | 0.13 | 81.271 | 89.229 | 85.064 | 87.515 |
| usnyc | v22 selected | fixed-4m | 0.35 | 100.000 | 8.935 | 16.404 | 10.925 |
| usnyc | v21 parent | crown-half-radius-2-4m | 0.13 | 72.267 | 76.867 | 74.496 | 75.900 |
| usnyc | v21 parent | crown-half-radius-2-4m | 0.35 | 96.875 | 3.794 | 7.303 | 4.697 |
| usnyc | v21 parent | fixed-2m | 0.13 | 69.620 | 74.051 | 71.767 | 73.121 |
| usnyc | v21 parent | fixed-2m | 0.35 | 96.875 | 3.794 | 7.303 | 4.697 |
| usnyc | v21 parent | fixed-4m | 0.13 | 82.394 | 87.638 | 84.935 | 86.536 |
| usnyc | v21 parent | fixed-4m | 0.35 | 100.000 | 3.917 | 7.538 | 4.848 |
| usffx | v22 selected | crown-half-radius-2-4m | 0.13 | 41.071 | 56.557 | 47.586 | 52.591 |
| usffx | v22 selected | crown-half-radius-2-4m | 0.35 | 80.952 | 13.934 | 23.776 | 16.699 |
| usffx | v22 selected | fixed-2m | 0.13 | 39.881 | 54.918 | 46.207 | 51.067 |
| usffx | v22 selected | fixed-2m | 0.35 | 80.952 | 13.934 | 23.776 | 16.699 |
| usffx | v22 selected | fixed-4m | 0.13 | 52.976 | 72.951 | 61.379 | 67.835 |
| usffx | v22 selected | fixed-4m | 0.35 | 95.238 | 16.393 | 27.972 | 19.646 |
| usffx | v21 parent | crown-half-radius-2-4m | 0.13 | 40.645 | 51.639 | 45.487 | 48.989 |
| usffx | v21 parent | crown-half-radius-2-4m | 0.35 | 85.000 | 13.934 | 23.944 | 16.732 |
| usffx | v21 parent | fixed-2m | 0.13 | 40.645 | 51.639 | 45.487 | 48.989 |
| usffx | v21 parent | fixed-2m | 0.35 | 85.000 | 13.934 | 23.944 | 16.732 |
| usffx | v21 parent | fixed-4m | 0.13 | 54.194 | 68.852 | 60.650 | 65.319 |
| usffx | v21 parent | fixed-4m | 0.35 | 95.000 | 15.574 | 26.761 | 18.701 |
| usbos-naip-2025 | v22 selected | crown-half-radius-2-4m | 0.13 | 42.051 | 57.076 | 48.425 | 53.269 |
| usbos-naip-2025 | v22 selected | crown-half-radius-2-4m | 0.35 | 75.000 | 1.876 | 3.661 | 2.331 |
| usbos-naip-2025 | v22 selected | fixed-2m | 0.13 | 34.793 | 47.224 | 40.066 | 44.075 |
| usbos-naip-2025 | v22 selected | fixed-2m | 0.35 | 68.750 | 1.720 | 3.356 | 2.137 |
| usbos-naip-2025 | v22 selected | fixed-4m | 0.13 | 56.970 | 77.326 | 65.605 | 72.169 |
| usbos-naip-2025 | v22 selected | fixed-4m | 0.35 | 87.500 | 2.189 | 4.272 | 2.720 |
| usbos-naip-2025 | v21 parent | crown-half-radius-2-4m | 0.13 | 43.516 | 54.574 | 48.422 | 51.935 |
| usbos-naip-2025 | v21 parent | crown-half-radius-2-4m | 0.35 | 71.429 | 1.173 | 2.308 | 1.460 |
| usbos-naip-2025 | v21 parent | fixed-2m | 0.13 | 36.222 | 45.426 | 40.305 | 43.229 |
| usbos-naip-2025 | v21 parent | fixed-2m | 0.35 | 66.667 | 1.095 | 2.154 | 1.363 |
| usbos-naip-2025 | v21 parent | fixed-4m | 0.13 | 58.791 | 73.729 | 65.418 | 70.164 |
| usbos-naip-2025 | v21 parent | fixed-4m | 0.35 | 90.476 | 1.486 | 2.923 | 1.849 |

## Classification and vegetation

Classification is conditional on matched detections. Changes in which trees are matched can change these scores. Vegetation categories are reported separately; sparse category support does not establish broad quality.

| City/acquisition | Model | Point | Threshold | Genus n | Genus% | Species n | Species% | Vegetation n | Vegetation% | Species top5% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v22 selected | best F2 | 0.14 | 4075 | 53.252 | 3688 | 57.321 | 1 | 0.000 | 79.691 |
| ussfo | v21 parent | best F2 | 0.13 | 4089 | 52.140 | 3700 | 56.297 | 1 | 0.000 | 79.432 |
| usbos | v22 selected | best F2 | 0.12 | 1648 | 39.684 | 1204 | 46.013 | 1 | 0.000 | 70.432 |
| usbos | v21 parent | best F2 | 0.13 | 1652 | 40.678 | 1194 | 46.482 | 1 | 0.000 | 70.352 |
| usnyc | v22 selected | best F2 | 0.14 | 411 | 20.681 | 360 | 21.667 | 0 | n/a | 54.444 |
| usnyc | v21 parent | best F2 | 0.12 | 407 | 22.113 | 359 | 22.006 | 0 | n/a | 55.710 |
| usffx | v22 selected | best F2 | 0.13 | 2 | 0.000 | 2 | 0.000 | 0 | n/a | 0.000 |
| usffx | v21 parent | best F2 | 0.14 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usbos-naip-2025 | v22 selected | best F2 | 0.1 | 351 | 13.105 | 90 | 38.889 | 0 | n/a | 47.778 |
| usbos-naip-2025 | v21 parent | best F2 | 0.1 | 349 | 13.754 | 88 | 37.500 | 0 | n/a | 45.455 |
| ussfo | v22 selected | fixed | 0.13 | 4087 | 53.120 | 3697 | 57.236 | 1 | 0.000 | 79.578 |
| ussfo | v22 selected | fixed | 0.35 | 2675 | 53.682 | 2432 | 57.977 | 1 | 0.000 | 81.456 |
| ussfo | v21 parent | fixed | 0.13 | 4089 | 52.140 | 3700 | 56.297 | 1 | 0.000 | 79.432 |
| ussfo | v21 parent | fixed | 0.35 | 2642 | 52.990 | 2401 | 57.643 | 0 | n/a | 81.383 |
| usbos | v22 selected | fixed | 0.13 | 1626 | 39.668 | 1189 | 45.837 | 1 | 0.000 | 70.311 |
| usbos | v22 selected | fixed | 0.35 | 346 | 28.035 | 264 | 25.000 | 0 | n/a | 55.682 |
| usbos | v21 parent | fixed | 0.13 | 1652 | 40.678 | 1194 | 46.482 | 1 | 0.000 | 70.352 |
| usbos | v21 parent | fixed | 0.35 | 277 | 27.076 | 203 | 27.094 | 0 | n/a | 57.143 |
| usnyc | v22 selected | fixed | 0.13 | 411 | 20.681 | 360 | 21.667 | 0 | n/a | 54.444 |
| usnyc | v22 selected | fixed | 0.35 | 43 | 18.605 | 39 | 12.821 | 0 | n/a | 43.590 |
| usnyc | v21 parent | fixed | 0.13 | 405 | 21.975 | 357 | 21.849 | 0 | n/a | 55.742 |
| usnyc | v21 parent | fixed | 0.35 | 19 | 31.579 | 17 | 17.647 | 0 | n/a | 47.059 |
| usffx | v22 selected | fixed | 0.13 | 2 | 0.000 | 2 | 0.000 | 0 | n/a | 0.000 |
| usffx | v22 selected | fixed | 0.35 | 0 | n/a | 0 | n/a | 0 | n/a | n/a |
| usffx | v21 parent | fixed | 0.13 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usffx | v21 parent | fixed | 0.35 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usbos-naip-2025 | v22 selected | fixed | 0.13 | 320 | 13.750 | 84 | 40.476 | 0 | n/a | 50.000 |
| usbos-naip-2025 | v22 selected | fixed | 0.35 | 13 | 15.385 | 2 | 50.000 | 0 | n/a | 50.000 |
| usbos-naip-2025 | v21 parent | fixed | 0.13 | 318 | 14.151 | 83 | 37.349 | 0 | n/a | 45.783 |
| usbos-naip-2025 | v21 parent | fixed | 0.35 | 9 | 11.111 | 1 | 100.000 | 0 | n/a | 100.000 |

## Explicit single-tree occupancy

Occupancy is a region proxy, distinct from distance-matched precision/recall. Materialized ownership groups may be clipped portions of a drawn region. Zero duplicates with missed regions is not successful detection.

| City/acquisition | Validation tiles | Tiles with groups | Groups |
| --- | --- | --- | --- |
| ussfo | 220 | 3 | 4 |
| usbos | 89 | 8 | 20 |
| usnyc | 19 | 2 | 2 |
| usffx | 5 | 0 | 0 |
| usbos-naip-2025 | 10 | 6 | 17 |

| City/acquisition | Model | Threshold | Groups | Peaks | Covered | Missed | Duplicate groups | Excess peaks | Occupancy P% | Occupancy R% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v22 selected | 0.13 | 4 | 7 | 4 | 0 | 2 | 3 | 57.143 | 100.000 |
| ussfo | v22 selected | 0.35 | 4 | 0 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| ussfo | v21 parent | 0.13 | 4 | 9 | 4 | 0 | 3 | 5 | 44.444 | 100.000 |
| ussfo | v21 parent | 0.35 | 4 | 0 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| usbos | v22 selected | 0.13 | 20 | 47 | 20 | 0 | 12 | 27 | 42.553 | 100.000 |
| usbos | v22 selected | 0.35 | 20 | 0 | 0 | 20 | 0 | 0 | n/a | 0.000 |
| usbos | v21 parent | 0.13 | 20 | 46 | 20 | 0 | 13 | 26 | 43.478 | 100.000 |
| usbos | v21 parent | 0.35 | 20 | 0 | 0 | 20 | 0 | 0 | n/a | 0.000 |
| usnyc | v22 selected | 0.13 | 2 | 6 | 2 | 0 | 2 | 4 | 33.333 | 100.000 |
| usnyc | v22 selected | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | n/a | 0.000 |
| usnyc | v21 parent | 0.13 | 2 | 5 | 2 | 0 | 2 | 3 | 40.000 | 100.000 |
| usnyc | v21 parent | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | n/a | 0.000 |
| usffx | v22 selected | 0.13 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v22 selected | 0.35 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v21 parent | 0.13 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v21 parent | 0.35 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usbos-naip-2025 | v22 selected | 0.13 | 17 | 40 | 16 | 1 | 12 | 24 | 40.000 | 94.118 |
| usbos-naip-2025 | v22 selected | 0.35 | 17 | 1 | 1 | 16 | 0 | 0 | 100.000 | 5.882 |
| usbos-naip-2025 | v21 parent | 0.13 | 17 | 38 | 16 | 1 | 10 | 22 | 42.105 | 94.118 |
| usbos-naip-2025 | v21 parent | 0.35 | 17 | 0 | 0 | 17 | 0 | 0 | n/a | 0.000 |

## Geographic uncertainty

5000 paired resamples of 768 m geographic blocks, after exact agreement with scorer aggregate counts and metrics. Delta is v22 selected minus v21, in percentage points. Intervals are withheld below 10 blocks. Best-threshold intervals condition on selected thresholds; they exclude threshold-selection uncertainty. Boston 2023 and 2025 share geography and are reported separately, never pooled as independent locations. Dependence may extend beyond this block scale; these are validation diagnostics, not sealed-test generalization.

Point estimates alone do not establish robust geographic generalization gains.

| City/acquisition | Policy | Point | Blocks | Delta P pp | Delta R pp | Delta F1 pp | Delta F2 pp | F2 95% interval pp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | crown-half-radius-2-4m | .13 | 27 | 0.015 | -0.014 | 0.002 | -0.008 | [-0.371, 0.330] |
| ussfo | crown-half-radius-2-4m | .35 | 27 | -0.250 | 0.514 | 0.380 | 0.484 | [-0.642, 1.801] |
| ussfo | crown-half-radius-2-4m | validation_best | 27 | 1.075 | -0.329 | 0.434 | -0.007 | [-0.386, 0.356] |
| ussfo | fixed-2m | .13 | 27 | -0.051 | -0.086 | -0.067 | -0.078 | [-0.490, 0.297] |
| ussfo | fixed-2m | .35 | 27 | -0.136 | 0.571 | 0.456 | 0.548 | [-0.601, 1.869] |
| ussfo | fixed-2m | validation_best | 27 | -0.051 | -0.086 | -0.067 | -0.078 | [-0.490, 0.297] |
| ussfo | fixed-4m | .13 | 27 | 0.031 | 0.000 | 0.017 | 0.007 | [-0.202, 0.252] |
| ussfo | fixed-4m | .35 | 27 | -0.097 | 0.629 | 0.514 | 0.606 | [-0.479, 1.912] |
| ussfo | fixed-4m | validation_best | 27 | -1.094 | 0.314 | -0.451 | -0.008 | [-0.222, 0.225] |
| usbos | crown-half-radius-2-4m | .13 | 25 | 0.064 | -0.704 | -0.242 | -0.496 | [-1.085, 0.291] |
| usbos | crown-half-radius-2-4m | .35 | 25 | -0.662 | 2.249 | 3.496 | 2.632 | [1.748, 4.230] |
| usbos | crown-half-radius-2-4m | validation_best | 25 | -1.977 | 0.196 | -1.161 | -0.433 | [-0.975, 0.316] |
| usbos | fixed-2m | .13 | 25 | -0.061 | -0.763 | -0.343 | -0.573 | [-1.267, 0.404] |
| usbos | fixed-2m | .35 | 25 | -0.845 | 2.190 | 3.400 | 2.562 | [1.636, 4.212] |
| usbos | fixed-2m | validation_best | 25 | -0.061 | -0.763 | -0.343 | -0.573 | [-1.267, 0.404] |
| usbos | fixed-4m | .13 | 25 | 0.685 | -0.137 | 0.364 | 0.091 | [-0.465, 0.800] |
| usbos | fixed-4m | .35 | 25 | 0.004 | 2.621 | 4.095 | 3.071 | [1.930, 4.863] |
| usbos | fixed-4m | validation_best | 25 | -1.889 | 0.997 | -0.795 | 0.170 | [-0.454, 0.844] |
| usnyc | crown-half-radius-2-4m | .13 | 3 | 0.308 | 2.815 | 1.467 | 2.251 | withheld |
| usnyc | crown-half-radius-2-4m | .35 | 3 | -0.985 | 4.774 | 8.428 | 5.779 | withheld |
| usnyc | crown-half-radius-2-4m | validation_best | 3 | 4.121 | 1.346 | 2.849 | 1.978 | withheld |
| usnyc | fixed-2m | .13 | 3 | -0.612 | 1.714 | 0.461 | 1.189 | withheld |
| usnyc | fixed-2m | .35 | 3 | -2.354 | 4.651 | 8.203 | 5.629 | withheld |
| usnyc | fixed-2m | validation_best | 3 | 2.981 | 0.245 | 1.725 | 0.867 | withheld |
| usnyc | fixed-4m | .13 | 3 | -1.123 | 1.591 | 0.129 | 0.979 | withheld |
| usnyc | fixed-4m | .35 | 3 | 0.000 | 5.018 | 8.866 | 6.076 | withheld |
| usnyc | fixed-4m | validation_best | 3 | -1.663 | 1.346 | -0.336 | 0.624 | withheld |
| usffx | crown-half-radius-2-4m | .13 | 4 | 0.426 | 4.918 | 2.099 | 3.602 | withheld |
| usffx | crown-half-radius-2-4m | .35 | 4 | -4.048 | 0.000 | -0.167 | -0.033 | withheld |
| usffx | crown-half-radius-2-4m | validation_best | 4 | -2.079 | 4.918 | 0.571 | 2.907 | withheld |
| usffx | fixed-2m | .13 | 4 | -0.764 | 3.279 | 0.720 | 2.078 | withheld |
| usffx | fixed-2m | .35 | 4 | -4.048 | 0.000 | -0.167 | -0.033 | withheld |
| usffx | fixed-2m | validation_best | 4 | -1.379 | 2.459 | 0.128 | 1.399 | withheld |
| usffx | fixed-4m | .13 | 4 | -1.217 | 4.098 | 0.729 | 2.517 | withheld |
| usffx | fixed-4m | .35 | 4 | 0.238 | 0.820 | 1.211 | 0.946 | withheld |
| usffx | fixed-4m | validation_best | 4 | 1.514 | 0.820 | 1.311 | 1.068 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | .13 | 1 | -1.466 | 2.502 | 0.003 | 1.335 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | .35 | 1 | 3.571 | 0.704 | 1.354 | 0.871 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | validation_best | 1 | -0.763 | 3.284 | 0.275 | 1.660 | withheld |
| usbos-naip-2025 | fixed-2m | .13 | 1 | -1.429 | 1.798 | -0.239 | 0.846 | withheld |
| usbos-naip-2025 | fixed-2m | .35 | 1 | 2.083 | 0.625 | 1.202 | 0.774 | withheld |
| usbos-naip-2025 | fixed-2m | validation_best | 1 | 2.003 | 0.391 | 1.780 | 1.225 | withheld |
| usbos-naip-2025 | fixed-4m | .13 | 1 | -1.820 | 3.597 | 0.187 | 2.005 | withheld |
| usbos-naip-2025 | fixed-4m | .35 | 1 | -2.976 | 0.704 | 1.348 | 0.870 | withheld |
| usbos-naip-2025 | fixed-4m | validation_best | 1 | -1.947 | 2.893 | -0.778 | 0.885 | withheld |

## Learning curves and compute

| TensorBoard step | Validation loss | UTC |
| --- | --- | --- |
| 0 | 3.58343 | 2026-11-16T15:23:31.997667+00:00 |
| 286 | 3.62723 | 2026-11-16T15:30:07.113071+00:00 |
| 572 | 3.58186 | 2026-11-16T15:36:30.062749+00:00 |
| 859 | 3.6488 | 2026-11-16T15:42:47.385833+00:00 |
| 1145 | 3.59783 | 2026-11-16T15:49:04.623719+00:00 |
| 1432 | 3.64332 | 2026-11-16T15:55:18.630920+00:00 |
| 1718 | 3.65011 | 2026-11-16T16:01:28.291295+00:00 |
| 2005 | 3.64389 | 2026-11-16T16:07:38.662945+00:00 |
| 2291 | 3.62819 | 2026-11-16T16:13:58.948931+00:00 |
| 2578 | 3.66784 | 2026-11-16T16:20:19.624487+00:00 |
| 2864 | 3.63845 | 2026-11-16T16:26:41.034194+00:00 |
| 3151 | 3.69912 | 2026-11-16T16:32:58.469316+00:00 |
| 3437 | 3.71347 | 2026-11-16T16:39:16.801285+00:00 |
| 3626 | 3.71099 | 2026-11-16T16:43:26.813432+00:00 |

| Component | Step-zero parent | Best-trained check | Final |
| --- | --- | --- | --- |
| validation/loss | 3.58343 | 3.58186 | 3.71099 |
| validation/center_loss | 1.3075 | 1.30949 | 1.29525 |
| validation/single_tree_peak_loss | 0.00290512 | 0.00263814 | 0.00227122 |
| validation/dbh_loss | 0.111987 | 0.112757 | 0.111348 |
| validation/crown_loss | 0.0137608 | 0.0140369 | 0.0148771 |
| validation/genus_loss | 2.20813 | 2.19958 | 2.33215 |
| validation/species_loss | 2.1984 | 2.19906 | 2.35369 |

At the selected check, raw center-loss change was 0.00198364; weighted genus-plus-species change was -0.00394309. Aggregate loss improvement must therefore be interpreted alongside detection metrics, rather than treated as a direct false-positive reduction.

| Component | Tail checks | Per1000 steps | Per budget source pass | Per hour |
| --- | --- | --- | --- | --- |
| validation/loss | 3 | 0.0270176 | 0.00775404 | 0.0735268 |
| validation/loss | 5 | 0.0611226 | 0.0175422 | 0.166123 |
| validation/center_loss | 3 | -0.0591426 | -0.0169739 | -0.160953 |
| validation/center_loss | 5 | 0.00677299 | 0.00194385 | 0.0183622 |
| validation/single_tree_peak_loss | 3 | -0.000318294 | -9.13504e-05 | -0.000866224 |
| validation/single_tree_peak_loss | 5 | -0.000154136 | -4.42372e-05 | -0.000422763 |
| validation/dbh_loss | 3 | -0.000233101 | -6.68999e-05 | -0.000634298 |
| validation/dbh_loss | 5 | 0.00123002 | 0.000353017 | 0.00335282 |
| validation/crown_loss | 3 | 0.00156577 | 0.000449375 | 0.00426115 |
| validation/crown_loss | 5 | 0.00105808 | 0.000303669 | 0.0028828 |
| validation/genus_loss | 3 | 0.0803142 | 0.0230502 | 0.21857 |
| validation/genus_loss | 5 | 0.0479679 | 0.0137668 | 0.130404 |
| validation/species_loss | 3 | 0.0897424 | 0.0257561 | 0.244228 |
| validation/species_loss | 5 | 0.0576946 | 0.0165583 | 0.156849 |

Best trained TensorBoard step 572; checkpoint global_step 573. Final exposure approximately 12.6341 budget source passes. TensorBoard/checkpoint steps can differ by one; source-pass conversion is a budget ratio. Declining training loss, early stopping, or a wall-clock cutoff does not prove convergence.

Baseline completion to final validation: 4794.816s; supervisor runtime: 10024.223 s; allocation through job finish: 11168.575 s. Maximum sampled VRAM: 2363.0 MiB (not a measured peak). Provider billing duration and exact FLOPs are unavailable.

## Teardown and reproducibility

Exact instance 15a4495d190b465a9a2e6fa7fe782647 is API-verified TERMINATED with a null IP. Supervisor exit 0 at 2026-11-16T16:49:30.205701+00:00; collector exit 0 with archive checksum and download acknowledgment. Independent deployment/joint deadlines were verified at17:43:21UTC, with optimization cutoff16:43:21UTC. No extension or recovery instance was used.

Results archive SHA256 `e792af012026312f64c1ef5e0c8cb9bfb42b69f8d28b42d186e3972d06323f49`. Source SHA256 `7ea8ce2c2a45fc1e8372f5b47154c9824eb49b2833f4506d6cb233dde9a2f862`; inputs SHA256 `e3fa5aea31e3d6aa70089f6dbf2664ccbfa0c28bd82fd8a057f7e4ff1a9ad7db`. Storage/upload hashes, actual Rust/CUDA focal/crown and all three 512 px batch 2 backward preflights passed. Offline scoring used this run’s archived source and locked Linux environment; all ten paired validation label/mask tables reproduced exactly. Actual remote VRTs were captured with path-only local relocation proofs. Fairfax and Boston 2025 were rebuilt as full frozen development cohorts to retain admission checks, then exact validation rows/pixels projected for scoring. Local CPU containers were bounded to 2 CPU/4 GiB.

## Confirmed-background false detections

Frozen admitted validation only; explicit binary background mask clears positive support, single-tree/group regions, and excluded cells. Counts are peaks in trusted empty areas, not distance-matched precision/recall. Training-only Boston2021 has no validation evidence here.

| City/acquisition | Split | Tiles | Tiles with explicit background | Background output cells |
| --- | --- | --- | --- | --- |
| ussfo | train | 4468 | 175 | 173876 |
| ussfo | validation | 220 | 31 | 42495 |
| usbos | validation | 89 | 26 | 60389 |
| usbos | train | 4624 | 294 | 1577477 |
| usnyc | train | 7 | 4 | 8271 |
| usnyc | validation | 19 | 8 | 15282 |
| usffx | train | 9 | 7 | 42408 |
| usffx | validation | 5 | 4 | 40471 |
| usbos-naip-2021 | train | 24 | 24 | 1568070 |
| usbos-naip-2025 | train | 30 | 16 | 788962 |
| usbos-naip-2025 | validation | 10 | 9 | 6656 |

This run combines the latest curation with fine-tuning under the doubled background penalty. There is no matched 1x retraining control, so changes cannot be attributed uniquely to the weighting.

| City/acquisition | Validation tiles | Tiles with background | Background output cells |
| --- | --- | --- | --- |
| ussfo | 220 | 31 | 42495 |
| usbos | 89 | 26 | 60389 |
| usnyc | 19 | 8 | 15282 |
| usffx | 5 | 4 | 40471 |
| usbos-naip-2025 | 10 | 9 | 6656 |

| City/acquisition | Model | Threshold | Tree peaks on background | Vegetation peaks on background | Tiles with peaks |
| --- | --- | --- | --- | --- | --- |
| ussfo | v22 selected | 0.12 | 98 | 0 | 27 |
| ussfo | v22 selected | 0.13 | 90 | 0 | 27 |
| ussfo | v22 selected | 0.35 | 2 | 0 | 2 |
| ussfo | v21 parent | 0.12 | 98 | 0 | 27 |
| ussfo | v21 parent | 0.13 | 85 | 0 | 26 |
| ussfo | v21 parent | 0.35 | 2 | 0 | 2 |
| usbos | v22 selected | 0.12 | 122 | 0 | 21 |
| usbos | v22 selected | 0.13 | 93 | 0 | 20 |
| usbos | v22 selected | 0.35 | 2 | 0 | 2 |
| usbos | v21 parent | 0.12 | 124 | 0 | 21 |
| usbos | v21 parent | 0.13 | 107 | 0 | 20 |
| usbos | v21 parent | 0.35 | 2 | 0 | 2 |
| usnyc | v22 selected | 0.12 | 34 | 0 | 7 |
| usnyc | v22 selected | 0.13 | 28 | 0 | 7 |
| usnyc | v22 selected | 0.35 | 0 | 0 | 0 |
| usnyc | v21 parent | 0.12 | 37 | 0 | 7 |
| usnyc | v21 parent | 0.13 | 35 | 0 | 7 |
| usnyc | v21 parent | 0.35 | 0 | 0 | 0 |
| usffx | v22 selected | 0.12 | 41 | 0 | 4 |
| usffx | v22 selected | 0.13 | 32 | 0 | 4 |
| usffx | v22 selected | 0.35 | 1 | 0 | 1 |
| usffx | v21 parent | 0.12 | 38 | 0 | 4 |
| usffx | v21 parent | 0.13 | 28 | 0 | 4 |
| usffx | v21 parent | 0.35 | 1 | 0 | 1 |
| usbos-naip-2025 | v22 selected | 0.12 | 29 | 0 | 9 |
| usbos-naip-2025 | v22 selected | 0.13 | 28 | 0 | 9 |
| usbos-naip-2025 | v22 selected | 0.35 | 0 | 0 | 0 |
| usbos-naip-2025 | v21 parent | 0.12 | 32 | 0 | 9 |
| usbos-naip-2025 | v21 parent | 0.13 | 29 | 0 | 9 |
| usbos-naip-2025 | v21 parent | 0.35 | 0 | 0 | 0 |

Selected model on its own training tiles only; no paired-parent training inference and no generalization inference. Includes training-only Boston2021.

| Training acquisition | Threshold | Background tiles | Selected tree peaks on background | Selected vegetation peaks on background | Tiles with peaks |
| --- | --- | --- | --- | --- | --- |
| ussfo | 0.12 | 175 | 378 | 0 | 132 |
| ussfo | 0.13 | 175 | 333 | 0 | 132 |
| ussfo | 0.35 | 175 | 30 | 0 | 24 |
| usbos | 0.12 | 294 | 1056 | 0 | 227 |
| usbos | 0.13 | 294 | 852 | 0 | 212 |
| usbos | 0.35 | 294 | 30 | 0 | 25 |
| usnyc | 0.12 | 4 | 26 | 0 | 4 |
| usnyc | 0.13 | 4 | 26 | 0 | 4 |
| usnyc | 0.35 | 4 | 0 | 0 | 0 |
| usffx | 0.12 | 7 | 36 | 0 | 7 |
| usffx | 0.13 | 7 | 27 | 0 | 7 |
| usffx | 0.35 | 7 | 0 | 0 | 0 |
| usbos-naip-2021 | 0.12 | 24 | 14 | 0 | 11 |
| usbos-naip-2021 | 0.13 | 24 | 7 | 0 | 6 |
| usbos-naip-2021 | 0.35 | 24 | 0 | 0 | 0 |
| usbos-naip-2025 | 0.12 | 16 | 10 | 0 | 5 |
| usbos-naip-2025 | 0.13 | 16 | 9 | 0 | 5 |
| usbos-naip-2025 | 0.35 | 16 | 0 | 0 | 0 |

Selected model only on 24 original Logan training tiles, all with zero curated targets and nonempty trusted supervision. Main count uses stored detection_mask_value > 0 at decoded tree peaks. Separately, 15 tiles have an explicit confirmed-background mask; the other nine are not silently treated as explicit-background coverage. No paired-parent training comparison; no claim of generalization.

| Original Logan training tiles | Threshold | Background tree peaks | Tiles with tree peaks |
| --- | --- | --- | --- |
| 24 | 0.12 | 6 | 5 |
| 24 | 0.13 | 4 | 3 |
| 24 | 0.35 | 0 | 0 |

## Receipts

- [sf-boston-nyc-fairfax-naip-swin-30cm-v22-curated-finetune-background-supervision-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-fairfax-naip-swin-30cm-v22-curated-finetune-background-supervision-audit.json)
- [confirmed-background-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/confirmed-background-audit.json)
- [sf-boston-nyc-fairfax-naip-swin-30cm-v22-curated-finetune-completion-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-fairfax-naip-swin-30cm-v22-curated-finetune-completion-audit.json)
- [training-analysis.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/qa/confirmed-background-v22/training-analysis.json)
- [provenance.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/frozen-comparison/provenance.json)
- [best.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/frozen-comparison/best.json)
- [fixed.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/frozen-comparison/fixed.json)
- [both-models-mask-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/both-models-mask-audit.json)
- [metrics.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/occupancy/metrics.json)
- [geographic-uncertainty.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/geographic-uncertainty.json)
- [vrt-capture-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/vrt-capture-audit.json)
- [logan-training-background.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v22-linux-reproduction/output/logan-training-background.json)
