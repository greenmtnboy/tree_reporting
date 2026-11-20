# Detection microscope: Logan pilot

Built 2026-11-14 UTC for the user's first interpretability question: why does the detector predict trees on confirmed background?

## Open the experience

Open the self-contained `index.html` in shared artifacts under `benchmarks/v20-detection-microscope-pilot-20261113/`. No server, model runtime, or internet is needed to inspect it. A separate localhost preview server launch was rejected by tool policy; no alternative server launch was attempted. The curator was not restarted or changed.

Choose a tile and sampled peak, select a channel, compare its strongest real image crops, and move the channel multiplier toward zero. Add observations and export the candidate JSON before closing; candidates are in browser memory and do not publish curation. “Open in curator” links to the same acquisition and tile for normal review.

## Scope and provenance

- Promoted v20: `sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune`, checkpoint `001-0000569.ckpt`, SHA256 `ab428f589573be7666f840d467f672ad3144b50a948a8b621cc6eb0ceec86c9d`.
- This is the explicitly user-promoted checkpoint, not the parent retained by the original automatic training selection.
- Fresh CPU float32 replay on 12 Boston 2025 Logan TRAIN tiles and four curated TRAIN comparison tiles. No validation/test evaluation, training, GPU allocation, or promotion.
- 128 native post-ReLU channels immediately before the final center-head 1×1 convolution. Scores use the usual sigmoid; they are not calibrated probabilities.
- Frozen copies of saved reviews and scene metadata supply background-region badges. Such badges say where a sampled peak falls; low-score local maxima are shown too, so they are not a false-positive count.
- `atlas.json` records checkpoint, input chips, normalization, configuration, source and curation hashes. Heatmaps are quantized 32×32 mean-pooled previews at a shared per-channel scale. Point contributions use full-resolution activations.

## What the intervention means

The readout is `logit = bias + sum(weight[channel] * activation[channel])`. The slider scales one channel at the selected point, holding other activations fixed, then applies sigmoid. This is an exact intervention at the final linear readout; it does not explain all upstream Swin computation or establish that suppressing a channel would improve a trained model. The real-image crop gallery shows the strongest response per tile within this small pilot; it is neither a representative dataset search nor the full receptive field.

At the opening Logan point, channel 65 contributes approximately +0.197 logit. Zeroing it changes the score from 21.0% to 17.9%. This is a control demonstration, not an identified semantic cause. The gallery includes both runway and tree-containing contexts; naming a channel from a single example would be premature.

## Validation

- Four focused tests cover exact signed reconstruction, intervention arithmetic, unchanged weights/input, eval/readout guards, hook cleanup and production peak selection.
- On all 16 real tiles, maximum reconstruction discrepancy was 5.245208740234375e-06 logit. Independently executing the actual final layer with one channel zeroed agreed with the analytic intervention to at most 2.86102294921875e-06.
- Isolated headless browser checks verified 16 tiles, 128 channels, eight crop examples, selection/filtering, score changes and exported provenance. No JavaScript errors or mobile horizontal overflow. Desktop and mobile screenshots were visually inspected.
- Browser receipt and screenshots are beside the HTML. QA candidate export is synthetic test data, not a curation recommendation.

## Implementation

`src/urban_tree_ml/interpretability.py` captures and validates the final readout. `lambda/build_detection_microscope.py` builds a bounded pilot with Windows affinity mask 3, BelowNormal priority and two Torch threads. It refuses an existing output directory. `src/urban_tree_ml/detection_microscope.html` renders the static atlas. `tests/test_interpretability.py` covers the extraction primitives.

From the active imagery checkout, use the existing geospatial/Torch interpreter with `PYTHONPATH=src;lambda` and the shared artifact root:

```text
python lambda/build_detection_microscope.py --root <shared-artifacts> --run sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune --output <new-output-directory>
```

## Next evidence to collect

Start by comparing channels shared by confirmed-background peaks and genuine trees, recording hypotheses with concrete examples. Expand the atlas to matched city/year examples and separate repeated geographic locations when assessing coverage. Then add controlled RGB/NIR and context perturbations and selected upstream-layer interventions, with unchanged-image controls. Use recurring failure patterns to select diverse review candidates; assess any resulting training change separately on the established frozen validation cohort. Keep sealed tests sealed.

The design borrows the emphasis on native features, real activating examples and interventions from Goodfire's [Neural Networks Are Already Clean](https://already-clean.goodfire.com/) and [ConvNeXt microscope](https://already-clean.goodfire.com/microscope/medium.html). Their broader interpretability hypothesis is not a demonstrated property of our detector.

## Species workbench (2026-11-15)

The user asked to lead with species accuracy and confusions, then use native channels to guide training collections. The new self-contained page is shared `artifacts/benchmarks/v20-species-microscope-20261115/index.html` (about 33 MB). It is local-only and separate from the curator.

Fresh CPU float32 inference used the same explicitly promoted v20 step569 checkpoint above. Inputs came from the previously verified locked-Linux v20 occupancy reconstruction, with the original city normalization files. All321 frozen validation tiles were inspected;298 have eligible species-head supervision. Of6825 supervised centers, four vegetation-category centers are excluded from the species list, leaving6821 across97 species. There was no new reconstruction, inference on test data, training, GPU work, annotation mutation, promotion or curator restart.

This is top-1/top-5 **classification at labeled centers**, independent of detection or a confidence threshold. It is not end-to-end species detection accuracy or the original bf16 training loss. The original v20 retained-parent inference is deliberately not attributed to this promoted checkpoint. Frozen species labels can themselves be wrong; tile review does not imply field-verified species identity.

| City | Species centers | Top-1 correct | Top-5 correct |
| --- | ---: | ---: | ---: |
| SF | 4668 | 2517 | 3609 |
| Boston | 1805 | 875 | 1264 |
| NYC | 340 | 80 | 190 |
| Fairfax | 8 | 0 | 1 |

The species table keeps perfect and zero-support rows visible, supports city filtering, and shows distinct-tile support and frozen training-label coverage. Training counts distinguish label rows from unique inventory IDs; they are not augmented presentations or an exact masked-loss denominator. Counts exclude curation added since this run.

Click a rival species to compare directional errors, reverse errors, correct selected-species examples and correct rival examples. Shared channels rank the minimum of the two species' mean activation divided by that channel's maximum in the pair. Contrasting channels rank absolute mean `activation * (weight_rival - weight_selected)` on selected-to-rival errors. The bias difference is displayed separately. Shared activation is descriptive, not a semantic or causal conclusion. Channels are from the species head and differ from detection-head channels with the same number.

Examples default to the strongest response from distinct tiles first. A point-level multiplier recomputes all99 class logits and their softmax, not just the two selected classes. The export is a collection specification: evaluation evidence is explicitly validation and `training_eligible=false`. Collect separately curated TRAIN examples of both species under matched city/year/size/context conditions; preserve geographic splits and do not count repeat years as independent trees.

Example starting point: Pyrus calleryana is correct on86/274 centers (31.4%; top5 183/274,66.8%) across77 tiles. It is predicted as Gleditsia triacanthos27times, with one reverse mistake. Inspect shadow/context and label alignment alongside correct controls before prescribing additional examples. This observation does not establish the mechanism or expected retraining benefit.

Implementation: `species_microscope.py`, `species_microscope.html`, `lambda/build_species_microscope.py`, and `lambda/render_species_microscope.py`. The one completed CPU build used two threads, affinity3 and BelowNormal priority, taking733seconds. Do not duplicate it; render from the saved atlas for UI changes. Seven focused tests passed. Independent audit matched every supervised center to the frozen NPZ masks, reconstructed every top-1 and top-5 prediction, and checked confidence agreement within6.84e-7. Actual-layer reconstruction and channel-zeroing errors were each at most9.54e-6 logit. Isolated desktop/mobile browser QA passed city filtering, zero-support handling, channel controls, pair-specific notes, guarded exports, no horizontal overflow, no JavaScript errors and no network writes. Receipts, QA exports and screenshots are beside the page; QA notes are synthetic test data.
