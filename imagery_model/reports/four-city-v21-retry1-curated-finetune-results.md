# Four-city, multi-year v21 retry1 fine-tune results

Automatic selection chose **trained**, validation loss **3.577466965** against same-cohort promoted-v20 baseline **3.614672899**. Best trained loss was **3.577466965**. Stop reason: **wall_clock_training_cutoff**, true last optimizer step **3883**. Eligible for next-training-parent advancement: **True**. Live curator model defaults and annotations were not changed.

Selected checkpoint: **002-0001146.ckpt**, SHA256 `1393e64ef61d145648f3cf792e0c7fac6a391c9efd4760c75042b6170457a3c6`, run `sf-boston-nyc-fairfax-naip-swin-30cm-v21-curated-finetune-retry1`.

The comparison parent is the explicitly promoted v20 checkpoint 001-0000569.ckpt, SHA256 ab428f589573be7666f840d467f672ad3144b50a948a8b621cc6eb0ceec86c9d. It was freshly inferred on this run’s frozen validation inputs using the same decoder. Original cached v20 selected-parent outputs represent different weights and were not used.

At the common 0.13 threshold, crown-aware detection changes are:

| City/acquisition | v20 F2% | v21 F2% | Delta F2 pp | Delta TP | Delta FP |
| --- | --- | --- | --- | --- | --- |
| ussfo | 77.071 | 77.012 | -0.058 | 6 | 60 |
| usbos | 62.654 | 63.311 | 0.657 | 33 | -51 |
| usnyc | 75.768 | 75.900 | 0.132 | -3 | -24 |
| usffx | 45.669 | 48.989 | 3.320 | 5 | 3 |
| usbos-naip-2025 | 50.125 | 51.935 | 1.809 | 17 | -90 |

City keys: ussfo=SF 2022, usbos=Boston 2023, usnyc=NYC 2023, usffx=Fairfax 2023. Boston 2025 is a separate acquisition. Positive delta FP means more false positives. The comparison has city-specific gains and regressions; uncertainty and classification support are detailed below.

## Admission and training recipe

SwinTiny RGBN, 99 classes, 512 px at 30 cm; weights-only initialization with a fresh optimizer, LR 3e-5, batch 2 / accumulation 16, bf16, fixed_pair_v1/source_passes_v1, patience 12, maximum 80 source passes. Loss coefficients: center 1, DBH 0.5, crown 1, genus 0.5, species 0.5, explicit single-tree 1. Published curation admits partial and tree-free training tiles with trusted supervision only. New-year tiles require published human curation; uncurated temporal tiles are excluded. Validation requires explicit full-tile More done. No sealed test data were evaluated.

| City/acquisition | Train tiles | Train targets | Validation tiles | Validation targets |
| --- | --- | --- | --- | --- |
| ussfo | 4468 | 142140 | 220 | 7000 |
| usbos | 4625 | 101923 | 89 | 5113 |
| usnyc | 7 | 731 | 19 | 817 |
| usffx | 9 | 179 | 5 | 122 |
| usbos-naip-2021 | 24 | 0 | 0 | 0 |
| usbos-naip-2025 | 30 | 505 | 10 | 1279 |

Boston 2021 contributes 24 curated background training tiles and no validation tiles. Boston 2025 contributes 30 training and 10 validation tiles. Fairfax contributes 9 training and 5 validation tiles. SF 2020/SF 2024 have no eligible published curation and are excluded. The six frozen curation snapshots are byte-identical to the original v21 attempt; the retry changes run paths and the audited deployment/cutoff implementation, without refreezing newer edits.

The full step-zero baseline covered 343 validation windows. Selected checkpoint, best trained checkpoint, true last state, callbacks, and training result agree. The three best trained checkpoints and true last are retained.

| Inference cohort | Tiles | Targets | Predictions |
| --- | --- | --- | --- |
| train-ussfo | 4468 | 142140 | 2004659 |
| validation | 220 | 7000 | 95411 |
| parent-validation | 220 | 7000 | 93203 |
| train-usbos | 4625 | 101923 | 1910670 |
| validation-usbos | 89 | 5113 | 36821 |
| parent-validation-usbos | 89 | 5113 | 37856 |
| train-usnyc | 7 | 731 | 2948 |
| validation-usnyc | 19 | 817 | 9040 |
| parent-validation-usnyc | 19 | 817 | 9149 |
| train-usffx | 9 | 179 | 4474 |
| validation-usffx | 5 | 122 | 2560 |
| parent-validation-usffx | 5 | 122 | 2548 |
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
| ussfo | v21 selected | crown-half-radius-2-4m | 0.13 | 71.397 | 78.557 | 74.806 | 77.012 |
| ussfo | v21 selected | fixed-2m | 0.13 | 70.267 | 77.314 | 73.623 | 75.794 |
| ussfo | v21 selected | fixed-4m | 0.14 | 79.844 | 86.300 | 82.947 | 84.927 |
| ussfo | v20 promoted parent | crown-half-radius-2-4m | 0.14 | 73.194 | 78.171 | 75.601 | 77.123 |
| ussfo | v20 promoted parent | fixed-2m | 0.14 | 71.883 | 76.771 | 74.247 | 75.741 |
| ussfo | v20 promoted parent | fixed-4m | 0.14 | 80.591 | 86.071 | 83.241 | 84.917 |
| usbos | v21 selected | crown-half-radius-2-4m | 0.13 | 53.381 | 66.399 | 59.182 | 63.311 |
| usbos | v21 selected | fixed-2m | 0.13 | 46.855 | 58.283 | 51.948 | 55.572 |
| usbos | v21 selected | fixed-4m | 0.13 | 66.714 | 82.985 | 73.965 | 79.125 |
| usbos | v20 promoted parent | crown-half-radius-2-4m | 0.12 | 50.721 | 66.693 | 57.621 | 62.741 |
| usbos | v20 promoted parent | fixed-2m | 0.13 | 46.362 | 57.833 | 51.466 | 55.106 |
| usbos | v20 promoted parent | fixed-4m | 0.12 | 63.677 | 83.728 | 72.339 | 78.767 |
| usnyc | v21 selected | crown-half-radius-2-4m | 0.12 | 70.764 | 78.213 | 74.302 | 76.600 |
| usnyc | v21 selected | fixed-2m | 0.12 | 68.217 | 75.398 | 71.628 | 73.843 |
| usnyc | v21 selected | fixed-4m | 0.12 | 80.509 | 88.984 | 84.535 | 87.149 |
| usnyc | v20 promoted parent | crown-half-radius-2-4m | 0.12 | 68.090 | 78.091 | 72.748 | 75.862 |
| usnyc | v20 promoted parent | fixed-2m | 0.12 | 64.995 | 74.541 | 69.441 | 72.414 |
| usnyc | v20 promoted parent | fixed-4m | 0.12 | 77.801 | 89.229 | 83.124 | 86.683 |
| usffx | v21 selected | crown-half-radius-2-4m | 0.14 | 43.151 | 51.639 | 47.015 | 49.685 |
| usffx | v21 selected | fixed-2m | 0.14 | 43.151 | 51.639 | 47.015 | 49.685 |
| usffx | v21 selected | fixed-4m | 0.12 | 51.462 | 72.131 | 60.068 | 66.768 |
| usffx | v20 promoted parent | crown-half-radius-2-4m | 0.1 | 34.225 | 52.459 | 41.424 | 47.407 |
| usffx | v20 promoted parent | fixed-2m | 0.12 | 38.217 | 49.180 | 43.011 | 46.512 |
| usffx | v20 promoted parent | fixed-4m | 0.1 | 46.524 | 71.311 | 56.311 | 64.444 |
| usbos-naip-2025 | v21 selected | crown-half-radius-2-4m | 0.1 | 36.693 | 60.907 | 45.797 | 53.806 |
| usbos-naip-2025 | v21 selected | fixed-2m | 0.08 | 27.931 | 53.088 | 36.604 | 44.985 |
| usbos-naip-2025 | v21 selected | fixed-4m | 0.1 | 50.306 | 83.503 | 62.787 | 73.767 |
| usbos-naip-2025 | v20 promoted parent | crown-half-radius-2-4m | 0.07 | 31.441 | 64.504 | 42.275 | 53.295 |
| usbos-naip-2025 | v20 promoted parent | fixed-2m | 0.07 | 26.448 | 54.261 | 35.562 | 44.832 |
| usbos-naip-2025 | v20 promoted parent | fixed-4m | 0.09 | 45.339 | 86.317 | 59.451 | 73.103 |

### Fixed operating points

| City/acquisition | Model | Policy | Threshold | P% | R% | F1% | F2% |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v21 selected | crown-half-radius-2-4m | 0.13 | 71.397 | 78.557 | 74.806 | 77.012 |
| ussfo | v21 selected | crown-half-radius-2-4m | 0.35 | 92.500 | 48.629 | 63.745 | 53.725 |
| ussfo | v21 selected | fixed-2m | 0.13 | 70.267 | 77.314 | 73.623 | 75.794 |
| ussfo | v21 selected | fixed-2m | 0.35 | 91.957 | 48.343 | 63.371 | 53.409 |
| ussfo | v21 selected | fixed-4m | 0.13 | 78.720 | 86.614 | 82.479 | 84.911 |
| ussfo | v21 selected | fixed-4m | 0.35 | 97.201 | 51.100 | 66.985 | 56.455 |
| ussfo | v20 promoted parent | crown-half-radius-2-4m | 0.13 | 71.936 | 78.471 | 75.061 | 77.071 |
| ussfo | v20 promoted parent | crown-half-radius-2-4m | 0.35 | 93.375 | 45.300 | 61.004 | 50.500 |
| ussfo | v20 promoted parent | fixed-2m | 0.13 | 70.639 | 77.057 | 73.709 | 75.682 |
| ussfo | v20 promoted parent | fixed-2m | 0.35 | 92.874 | 45.057 | 60.677 | 50.229 |
| ussfo | v20 promoted parent | fixed-4m | 0.13 | 79.204 | 86.400 | 82.646 | 84.858 |
| ussfo | v20 promoted parent | fixed-4m | 0.35 | 97.556 | 47.329 | 63.736 | 52.761 |
| usbos | v21 selected | crown-half-radius-2-4m | 0.13 | 53.381 | 66.399 | 59.182 | 63.311 |
| usbos | v21 selected | crown-half-radius-2-4m | 0.35 | 85.050 | 10.014 | 17.918 | 12.159 |
| usbos | v21 selected | fixed-2m | 0.13 | 46.855 | 58.283 | 51.948 | 55.572 |
| usbos | v21 selected | fixed-2m | 0.35 | 83.887 | 9.877 | 17.673 | 11.993 |
| usbos | v21 selected | fixed-4m | 0.13 | 66.714 | 82.985 | 73.965 | 79.125 |
| usbos | v21 selected | fixed-4m | 0.35 | 95.017 | 11.187 | 20.017 | 13.584 |
| usbos | v20 promoted parent | crown-half-radius-2-4m | 0.13 | 52.712 | 65.754 | 58.515 | 62.654 |
| usbos | v20 promoted parent | crown-half-radius-2-4m | 0.35 | 85.786 | 10.033 | 17.965 | 12.185 |
| usbos | v20 promoted parent | fixed-2m | 0.13 | 46.362 | 57.833 | 51.466 | 55.106 |
| usbos | v20 promoted parent | fixed-2m | 0.35 | 85.117 | 9.955 | 17.825 | 12.090 |
| usbos | v20 promoted parent | fixed-4m | 0.13 | 65.945 | 82.261 | 73.205 | 78.382 |
| usbos | v20 promoted parent | fixed-4m | 0.35 | 95.485 | 11.168 | 19.996 | 13.563 |
| usnyc | v21 selected | crown-half-radius-2-4m | 0.13 | 72.267 | 76.867 | 74.496 | 75.900 |
| usnyc | v21 selected | crown-half-radius-2-4m | 0.35 | 96.875 | 3.794 | 7.303 | 4.697 |
| usnyc | v21 selected | fixed-2m | 0.13 | 69.620 | 74.051 | 71.767 | 73.121 |
| usnyc | v21 selected | fixed-2m | 0.35 | 96.875 | 3.794 | 7.303 | 4.697 |
| usnyc | v21 selected | fixed-4m | 0.13 | 82.394 | 87.638 | 84.935 | 86.536 |
| usnyc | v21 selected | fixed-4m | 0.35 | 100.000 | 3.917 | 7.538 | 4.848 |
| usnyc | v20 promoted parent | crown-half-radius-2-4m | 0.13 | 70.424 | 77.234 | 73.672 | 75.768 |
| usnyc | v20 promoted parent | crown-half-radius-2-4m | 0.35 | 95.082 | 7.099 | 13.212 | 8.711 |
| usnyc | v20 promoted parent | fixed-2m | 0.13 | 67.299 | 73.807 | 70.403 | 72.406 |
| usnyc | v20 promoted parent | fixed-2m | 0.35 | 95.082 | 7.099 | 13.212 | 8.711 |
| usnyc | v20 promoted parent | fixed-4m | 0.13 | 80.469 | 88.250 | 84.180 | 86.575 |
| usnyc | v20 promoted parent | fixed-4m | 0.35 | 100.000 | 7.466 | 13.895 | 9.162 |
| usffx | v21 selected | crown-half-radius-2-4m | 0.13 | 40.645 | 51.639 | 45.487 | 48.989 |
| usffx | v21 selected | crown-half-radius-2-4m | 0.35 | 85.000 | 13.934 | 23.944 | 16.732 |
| usffx | v21 selected | fixed-2m | 0.13 | 40.645 | 51.639 | 45.487 | 48.989 |
| usffx | v21 selected | fixed-2m | 0.35 | 85.000 | 13.934 | 23.944 | 16.732 |
| usffx | v21 selected | fixed-4m | 0.13 | 54.194 | 68.852 | 60.650 | 65.319 |
| usffx | v21 selected | fixed-4m | 0.35 | 95.000 | 15.574 | 26.761 | 18.701 |
| usffx | v20 promoted parent | crown-half-radius-2-4m | 0.13 | 39.456 | 47.541 | 43.123 | 45.669 |
| usffx | v20 promoted parent | crown-half-radius-2-4m | 0.35 | 80.000 | 13.115 | 22.535 | 15.748 |
| usffx | v20 promoted parent | fixed-2m | 0.13 | 39.456 | 47.541 | 43.123 | 45.669 |
| usffx | v20 promoted parent | fixed-2m | 0.35 | 80.000 | 13.115 | 22.535 | 15.748 |
| usffx | v20 promoted parent | fixed-4m | 0.13 | 53.061 | 63.934 | 57.993 | 61.417 |
| usffx | v20 promoted parent | fixed-4m | 0.35 | 95.000 | 15.574 | 26.761 | 18.701 |
| usbos-naip-2025 | v21 selected | crown-half-radius-2-4m | 0.13 | 43.516 | 54.574 | 48.422 | 51.935 |
| usbos-naip-2025 | v21 selected | crown-half-radius-2-4m | 0.35 | 71.429 | 1.173 | 2.308 | 1.460 |
| usbos-naip-2025 | v21 selected | fixed-2m | 0.13 | 36.222 | 45.426 | 40.305 | 43.229 |
| usbos-naip-2025 | v21 selected | fixed-2m | 0.35 | 66.667 | 1.095 | 2.154 | 1.363 |
| usbos-naip-2025 | v21 selected | fixed-4m | 0.13 | 58.791 | 73.729 | 65.418 | 70.164 |
| usbos-naip-2025 | v21 selected | fixed-4m | 0.35 | 90.476 | 1.486 | 2.923 | 1.849 |
| usbos-naip-2025 | v20 promoted parent | crown-half-radius-2-4m | 0.13 | 40.608 | 53.245 | 46.076 | 50.125 |
| usbos-naip-2025 | v20 promoted parent | crown-half-radius-2-4m | 0.35 | 61.111 | 0.860 | 1.696 | 1.071 |
| usbos-naip-2025 | v20 promoted parent | fixed-2m | 0.13 | 33.751 | 44.253 | 38.295 | 41.661 |
| usbos-naip-2025 | v20 promoted parent | fixed-2m | 0.35 | 55.556 | 0.782 | 1.542 | 0.974 |
| usbos-naip-2025 | v20 promoted parent | fixed-4m | 0.13 | 54.621 | 71.618 | 61.976 | 67.422 |
| usbos-naip-2025 | v20 promoted parent | fixed-4m | 0.35 | 83.333 | 1.173 | 2.313 | 1.461 |

## Classification and vegetation

Classification is conditional on matched detections. Changes in which trees are matched can change these scores. Vegetation categories are reported separately; sparse category support does not establish broad quality.

| City/acquisition | Model | Point | Threshold | Genus n | Genus% | Species n | Species% | Vegetation n | Vegetation% | Species top5% |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | v21 selected | best F2 | 0.13 | 4089 | 52.140 | 3700 | 56.297 | 1 | 0.000 | 79.432 |
| ussfo | v20 promoted parent | best F2 | 0.14 | 4077 | 52.220 | 3690 | 56.125 | 1 | 0.000 | 79.187 |
| usbos | v21 selected | best F2 | 0.13 | 1652 | 40.678 | 1194 | 46.482 | 1 | 0.000 | 70.352 |
| usbos | v20 promoted parent | best F2 | 0.12 | 1658 | 39.747 | 1210 | 45.950 | 1 | 0.000 | 68.760 |
| usnyc | v21 selected | best F2 | 0.12 | 407 | 22.113 | 359 | 22.006 | 0 | n/a | 55.710 |
| usnyc | v20 promoted parent | best F2 | 0.12 | 400 | 21.000 | 355 | 21.127 | 0 | n/a | 54.366 |
| usffx | v21 selected | best F2 | 0.14 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usffx | v20 promoted parent | best F2 | 0.1 | 2 | 0.000 | 2 | 0.000 | 0 | n/a | 0.000 |
| usbos-naip-2025 | v21 selected | best F2 | 0.1 | 349 | 13.754 | 88 | 37.500 | 0 | n/a | 45.455 |
| usbos-naip-2025 | v20 promoted parent | best F2 | 0.07 | 358 | 10.335 | 90 | 11.111 | 0 | n/a | 36.667 |
| ussfo | v21 selected | fixed | 0.13 | 4089 | 52.140 | 3700 | 56.297 | 1 | 0.000 | 79.432 |
| ussfo | v21 selected | fixed | 0.35 | 2642 | 52.990 | 2401 | 57.643 | 0 | n/a | 81.383 |
| ussfo | v20 promoted parent | fixed | 0.13 | 4087 | 52.190 | 3699 | 56.015 | 1 | 0.000 | 79.102 |
| ussfo | v20 promoted parent | fixed | 0.35 | 2479 | 52.521 | 2252 | 57.416 | 0 | n/a | 81.039 |
| usbos | v21 selected | fixed | 0.13 | 1652 | 40.678 | 1194 | 46.482 | 1 | 0.000 | 70.352 |
| usbos | v21 selected | fixed | 0.35 | 277 | 27.076 | 203 | 27.094 | 0 | n/a | 57.143 |
| usbos | v20 promoted parent | fixed | 0.13 | 1637 | 39.768 | 1195 | 45.774 | 1 | 0.000 | 68.787 |
| usbos | v20 promoted parent | fixed | 0.35 | 290 | 27.586 | 220 | 22.273 | 0 | n/a | 52.273 |
| usnyc | v21 selected | fixed | 0.13 | 405 | 21.975 | 357 | 21.849 | 0 | n/a | 55.742 |
| usnyc | v21 selected | fixed | 0.35 | 19 | 31.579 | 17 | 17.647 | 0 | n/a | 47.059 |
| usnyc | v20 promoted parent | fixed | 0.13 | 398 | 21.106 | 353 | 21.246 | 0 | n/a | 54.391 |
| usnyc | v20 promoted parent | fixed | 0.35 | 40 | 27.500 | 36 | 25.000 | 0 | n/a | 44.444 |
| usffx | v21 selected | fixed | 0.13 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usffx | v21 selected | fixed | 0.35 | 1 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |
| usffx | v20 promoted parent | fixed | 0.13 | 2 | 0.000 | 2 | 0.000 | 0 | n/a | 0.000 |
| usffx | v20 promoted parent | fixed | 0.35 | 0 | n/a | 0 | n/a | 0 | n/a | n/a |
| usbos-naip-2025 | v21 selected | fixed | 0.13 | 318 | 14.151 | 83 | 37.349 | 0 | n/a | 45.783 |
| usbos-naip-2025 | v21 selected | fixed | 0.35 | 9 | 11.111 | 1 | 100.000 | 0 | n/a | 100.000 |
| usbos-naip-2025 | v20 promoted parent | fixed | 0.13 | 310 | 10.323 | 81 | 11.111 | 0 | n/a | 39.506 |
| usbos-naip-2025 | v20 promoted parent | fixed | 0.35 | 7 | 0.000 | 1 | 0.000 | 0 | n/a | 0.000 |

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
| ussfo | v21 selected | 0.13 | 4 | 9 | 4 | 0 | 3 | 5 | 44.444 | 100.000 |
| ussfo | v21 selected | 0.35 | 4 | 0 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| ussfo | v20 promoted parent | 0.13 | 4 | 9 | 4 | 0 | 3 | 5 | 44.444 | 100.000 |
| ussfo | v20 promoted parent | 0.35 | 4 | 0 | 0 | 4 | 0 | 0 | n/a | 0.000 |
| usbos | v21 selected | 0.13 | 20 | 46 | 20 | 0 | 13 | 26 | 43.478 | 100.000 |
| usbos | v21 selected | 0.35 | 20 | 0 | 0 | 20 | 0 | 0 | n/a | 0.000 |
| usbos | v20 promoted parent | 0.13 | 20 | 52 | 20 | 0 | 18 | 32 | 38.462 | 100.000 |
| usbos | v20 promoted parent | 0.35 | 20 | 0 | 0 | 20 | 0 | 0 | n/a | 0.000 |
| usnyc | v21 selected | 0.13 | 2 | 5 | 2 | 0 | 2 | 3 | 40.000 | 100.000 |
| usnyc | v21 selected | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | n/a | 0.000 |
| usnyc | v20 promoted parent | 0.13 | 2 | 3 | 2 | 0 | 1 | 1 | 66.667 | 100.000 |
| usnyc | v20 promoted parent | 0.35 | 2 | 0 | 0 | 2 | 0 | 0 | n/a | 0.000 |
| usffx | v21 selected | 0.13 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v21 selected | 0.35 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v20 promoted parent | 0.13 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usffx | v20 promoted parent | 0.35 | 0 | 0 | 0 | 0 | 0 | 0 | n/a | n/a |
| usbos-naip-2025 | v21 selected | 0.13 | 17 | 38 | 16 | 1 | 10 | 22 | 42.105 | 94.118 |
| usbos-naip-2025 | v21 selected | 0.35 | 17 | 0 | 0 | 17 | 0 | 0 | n/a | 0.000 |
| usbos-naip-2025 | v20 promoted parent | 0.13 | 17 | 44 | 16 | 1 | 10 | 28 | 36.364 | 94.118 |
| usbos-naip-2025 | v20 promoted parent | 0.35 | 17 | 0 | 0 | 17 | 0 | 0 | n/a | 0.000 |

At 0.13, Boston 2023 excess peaks fell from 32 to 26, with all 20 groups still covered; Boston 2025 excess peaks fell from 28 to 22, with 16 of 17 groups covered by both models. SF was unchanged; NYC excess peaks increased from 1 to 3 across its two groups. At 0.35, both models missed every explicit single-tree group. Fairfax has no groups in this cohort.

## Geographic uncertainty

5000 paired resamples of 768 m geographic blocks, after exact agreement with scorer aggregate counts and metrics. Delta is v21 selected minus promoted v20, in percentage points. Intervals are withheld below 10 blocks. Best-threshold intervals condition on selected thresholds; they exclude threshold-selection uncertainty. Boston 2023 and 2025 share geography and are reported separately, never pooled as independent locations. Dependence may extend beyond this block scale; these are validation diagnostics, not sealed-test generalization.

At 0.13, the SF F2 delta interval is −0.576 to +0.386 percentage points and Boston 2023 is −0.422 to +1.468; both include zero. NYC has 3 blocks, Fairfax 4, and Boston 2025 only 1, so their intervals are withheld. The point estimates alone do not establish robust geographic generalization gains.

| City/acquisition | Policy | Point | Blocks | Delta P pp | Delta R pp | Delta F1 pp | Delta F2 pp | F2 95% interval pp |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ussfo | crown-half-radius-2-4m | .13 | 27 | -0.539 | 0.086 | -0.255 | -0.058 | [-0.576, 0.386] |
| ussfo | crown-half-radius-2-4m | .35 | 27 | -0.875 | 3.329 | 2.741 | 3.225 | [2.271, 4.054] |
| ussfo | crown-half-radius-2-4m | validation_best | 27 | -1.797 | 0.386 | -0.795 | -0.110 | [-0.681, 0.379] |
| ussfo | fixed-2m | .13 | 27 | -0.372 | 0.257 | -0.086 | 0.112 | [-0.322, 0.508] |
| ussfo | fixed-2m | .35 | 27 | -0.917 | 3.286 | 2.694 | 3.180 | [2.254, 3.994] |
| ussfo | fixed-2m | validation_best | 27 | -1.616 | 0.543 | -0.624 | 0.053 | [-0.424, 0.474] |
| ussfo | fixed-4m | .13 | 27 | -0.484 | 0.214 | -0.167 | 0.053 | [-0.304, 0.365] |
| ussfo | fixed-4m | .35 | 27 | -0.355 | 3.771 | 3.249 | 3.694 | [2.725, 4.555] |
| ussfo | fixed-4m | validation_best | 27 | -0.747 | 0.229 | -0.295 | 0.010 | [-0.388, 0.341] |
| usbos | crown-half-radius-2-4m | .13 | 25 | 0.668 | 0.645 | 0.667 | 0.657 | [-0.422, 1.468] |
| usbos | crown-half-radius-2-4m | .35 | 25 | -0.736 | -0.020 | -0.048 | -0.026 | [-1.474, 0.915] |
| usbos | crown-half-radius-2-4m | validation_best | 25 | 2.659 | -0.293 | 1.562 | 0.570 | [-0.531, 1.413] |
| usbos | fixed-2m | .13 | 25 | 0.493 | 0.450 | 0.482 | 0.466 | [-0.450, 1.200] |
| usbos | fixed-2m | .35 | 25 | -1.230 | -0.078 | -0.152 | -0.097 | [-1.566, 0.836] |
| usbos | fixed-2m | validation_best | 25 | 0.493 | 0.450 | 0.482 | 0.466 | [-0.450, 1.200] |
| usbos | fixed-4m | .13 | 25 | 0.768 | 0.724 | 0.760 | 0.743 | [-0.161, 1.405] |
| usbos | fixed-4m | .35 | 25 | -0.468 | 0.020 | 0.021 | 0.021 | [-1.440, 1.097] |
| usbos | fixed-4m | validation_best | 25 | 3.037 | -0.743 | 1.626 | 0.358 | [-0.437, 0.900] |
| usnyc | crown-half-radius-2-4m | .13 | 3 | 1.843 | -0.367 | 0.824 | 0.132 | withheld |
| usnyc | crown-half-radius-2-4m | .35 | 3 | 1.793 | -3.305 | -5.909 | -4.014 | withheld |
| usnyc | crown-half-radius-2-4m | validation_best | 3 | 2.674 | 0.122 | 1.554 | 0.738 | withheld |
| usnyc | fixed-2m | .13 | 3 | 2.321 | 0.245 | 1.365 | 0.714 | withheld |
| usnyc | fixed-2m | .35 | 3 | 1.793 | -3.305 | -5.909 | -4.014 | withheld |
| usnyc | fixed-2m | validation_best | 3 | 3.222 | 0.857 | 2.187 | 1.429 | withheld |
| usnyc | fixed-4m | .13 | 3 | 1.925 | -0.612 | 0.755 | -0.039 | withheld |
| usnyc | fixed-4m | .35 | 3 | 0.000 | -3.550 | -6.357 | -4.313 | withheld |
| usnyc | fixed-4m | validation_best | 3 | 2.708 | -0.245 | 1.411 | 0.467 | withheld |
| usffx | crown-half-radius-2-4m | .13 | 4 | 1.189 | 4.098 | 2.365 | 3.320 | withheld |
| usffx | crown-half-radius-2-4m | .35 | 4 | 5.000 | 0.820 | 1.408 | 0.984 | withheld |
| usffx | crown-half-radius-2-4m | validation_best | 4 | 8.926 | -0.820 | 5.591 | 2.277 | withheld |
| usffx | fixed-2m | .13 | 4 | 1.189 | 4.098 | 2.365 | 3.320 | withheld |
| usffx | fixed-2m | .35 | 4 | 5.000 | 0.820 | 1.408 | 0.984 | withheld |
| usffx | fixed-2m | validation_best | 4 | 4.934 | 2.459 | 4.004 | 3.173 | withheld |
| usffx | fixed-4m | .13 | 4 | 1.132 | 4.918 | 2.657 | 3.901 | withheld |
| usffx | fixed-4m | .35 | 4 | 0.000 | 0.000 | 0.000 | 0.000 | withheld |
| usffx | fixed-4m | validation_best | 4 | 4.938 | 0.820 | 3.758 | 2.323 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | .13 | 1 | 2.908 | 1.329 | 2.346 | 1.809 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | .35 | 1 | 10.317 | 0.313 | 0.611 | 0.389 | withheld |
| usbos-naip-2025 | crown-half-radius-2-4m | validation_best | 1 | 5.253 | -3.597 | 3.521 | 0.511 | withheld |
| usbos-naip-2025 | fixed-2m | .13 | 1 | 2.471 | 1.173 | 2.010 | 1.569 | withheld |
| usbos-naip-2025 | fixed-2m | .35 | 1 | 11.111 | 0.313 | 0.612 | 0.389 | withheld |
| usbos-naip-2025 | fixed-2m | validation_best | 1 | 1.483 | -1.173 | 1.041 | 0.153 | withheld |
| usbos-naip-2025 | fixed-4m | .13 | 1 | 4.169 | 2.111 | 3.442 | 2.741 | withheld |
| usbos-naip-2025 | fixed-4m | .35 | 1 | 7.143 | 0.313 | 0.610 | 0.388 | withheld |
| usbos-naip-2025 | fixed-4m | validation_best | 1 | 4.967 | -2.815 | 3.336 | 0.664 | withheld |

## Learning curves and compute

| TensorBoard step | Validation loss | UTC |
| --- | --- | --- |
| 0 | 3.61467 | 2026-11-15T23:48:44.678966+00:00 |
| 286 | 3.6262 | 2026-11-15T23:54:47.841085+00:00 |
| 573 | 3.66817 | 2026-11-16T00:00:52.461977+00:00 |
| 859 | 3.63846 | 2026-11-16T00:06:57.321542+00:00 |
| 1146 | 3.57747 | 2026-11-16T00:13:00.179814+00:00 |
| 1432 | 3.61639 | 2026-11-16T00:19:00.346811+00:00 |
| 1719 | 3.6527 | 2026-11-16T00:25:01.047724+00:00 |
| 2005 | 3.69054 | 2026-11-16T00:30:53.048629+00:00 |
| 2292 | 3.63864 | 2026-11-16T00:36:44.806381+00:00 |
| 2578 | 3.66896 | 2026-11-16T00:42:34.804202+00:00 |
| 2865 | 3.71343 | 2026-11-16T00:48:25.362279+00:00 |
| 3151 | 3.67691 | 2026-11-16T00:54:20.039515+00:00 |
| 3438 | 3.66933 | 2026-11-16T01:00:13.038306+00:00 |
| 3724 | 3.68581 | 2026-11-16T01:06:02.563613+00:00 |
| 3883 | 3.6942 | 2026-11-16T01:09:19.227132+00:00 |

| Component | Step-zero parent | Best-trained check | Final |
| --- | --- | --- | --- |
| validation/loss | 3.61467 | 3.57747 | 3.6942 |
| validation/center_loss | 1.30121 | 1.30153 | 1.31642 |
| validation/single_tree_peak_loss | 0.00311431 | 0.00290512 | 0.00220049 |
| validation/dbh_loss | 0.12008 | 0.111976 | 0.113693 |
| validation/crown_loss | 0.0159197 | 0.0137604 | 0.0153195 |
| validation/genus_loss | 2.23282 | 2.20814 | 2.29862 |
| validation/species_loss | 2.23596 | 2.19842 | 2.30821 |

At the selected check, raw center-loss change was 0.000325441; weighted genus-plus-species change was -0.0311108. Aggregate loss improvement must therefore be interpreted alongside detection metrics, rather than treated as a direct false-positive reduction.

| Component | Tail checks | Per1000 steps | Per budget source pass | Per hour |
| --- | --- | --- | --- | --- |
| validation/loss | 3 | 0.0560953 | 0.0160994 | 0.164602 |
| validation/loss | 5 | -0.0134804 | -0.00386888 | -0.0396877 |
| validation/center_loss | 3 | -0.0221328 | -0.0063521 | -0.0643719 |
| validation/center_loss | 5 | -0.0139222 | -0.00399568 | -0.0406511 |
| validation/single_tree_peak_loss | 3 | -0.000703358 | -0.000201864 | -0.00207152 |
| validation/single_tree_peak_loss | 5 | -6.46023e-05 | -1.85409e-05 | -0.00018586 |
| validation/dbh_loss | 3 | 0.00494489 | 0.00141918 | 0.0144866 |
| validation/dbh_loss | 5 | 0.0038446 | 0.0011034 | 0.0112502 |
| validation/crown_loss | 3 | 0.00142446 | 0.000408821 | 0.00418156 |
| validation/crown_loss | 5 | 0.000812949 | 0.000233316 | 0.00237623 |
| validation/genus_loss | 3 | 0.0532065 | 0.0152703 | 0.155536 |
| validation/genus_loss | 5 | -0.0056147 | -0.00161142 | -0.016777 |
| validation/species_loss | 3 | 0.0968583 | 0.0277983 | 0.283692 |
| validation/species_loss | 5 | 0.00115358 | 0.000331076 | 0.00306269 |

Best trained TensorBoard step 1146; checkpoint global_step 1146. Final exposure approximately 13.5296 budget source passes. TensorBoard/checkpoint steps can differ by one; source-pass conversion is a budget ratio. Declining training loss, early stopping, or a wall-clock cutoff does not prove convergence.

Baseline completion to final validation: 4834.548s; supervisor runtime: 9574.709 s; allocation through job finish: 11153.674 s. Maximum sampled VRAM: 2375.0 MiB (not a measured peak). Provider billing duration and exact FLOPs are unavailable.

## Teardown and reproducibility

Exact retry instance b4f5489cc6a548d1a7589c3a68500501 is API-verified TERMINATED with a null IP. Supervisor exit 0 at 2026-11-16T01:15:07.811201+00:00; collector exit 0 with archive checksum and download acknowledgment. Independent deployment/joint deadlines were verified at 02:09:14 UTC, with optimization cutoff 01:09:14 UTC. No extension or additional recovery instance was used. The original failed v21 deployment is separately documented and its instance was terminated before this authorized retry.

Results archive SHA256 `7c7fafc33dd36426a800944d8676680a7475d21957021b5c349e4b410ebb8cc3`. Source SHA256 `30835be6baf6fcb756fd3e30a14f9971f14656530b5c5eaf3431cb116e8f6a8b`; inputs SHA256 `f040260dc38a1efbbf21a1ee213e90a40391229ad5f4f2e723d432f3f396c59c`. Storage/upload hashes, actual Rust/CUDA focal/crown and all three 512 px batch 2 backward preflights passed. Offline scoring used this run’s archived source and locked Linux environment; all ten paired validation label/mask tables reproduced exactly. Actual remote VRTs were captured with path-only local relocation proofs. Fairfax and Boston 2025 were rebuilt as full frozen development cohorts to retain admission checks, then exact validation rows/pixels projected for scoring. Local CPU containers were bounded to 2 CPU/4 GiB.

## Receipts

- [sf-boston-nyc-fairfax-naip-swin-30cm-v21-curated-finetune-retry1-completion-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/sf-boston-nyc-fairfax-naip-swin-30cm-v21-curated-finetune-retry1-completion-audit.json)
- [training-analysis.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/qa/curation-admission-v21-retry1/training-analysis.json)
- [provenance.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/frozen-comparison/provenance.json)
- [best.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/frozen-comparison/best.json)
- [fixed.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/frozen-comparison/fixed.json)
- [both-models-mask-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/both-models-mask-audit.json)
- [metrics.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/occupancy/metrics.json)
- [geographic-uncertainty.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/output/geographic-uncertainty.json)
- [vrt-capture-audit.json](C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/benchmarks/v21-retry1-linux-reproduction/vrt-capture-audit.json)
