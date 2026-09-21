# DeepForest pretrained detection baseline

## Results — complete

All 567 SF and 636 Boston validation chips processed, including chips with no
detections. Strict weights load and input-range checks passed. The downloaded
archive was hash-verified before teardown acknowledgement. Lambda teardown was
confirmed at 22:14 UTC: no active instances remain. Ground-truth parquet
SHA256s are identical to Swin v2 in both cities. Visual checks of saved SF and
Boston smoke overlays showed plausible aligned crown boxes; this is not a
comprehensive qualitative audit.

Goal-aligned F2 at confidence 0.35:

| City | DeepForest crown-aware | Swin crown-aware | DeepForest 4 m | Swin 4 m |
|---|---:|---:|---:|---:|
| SF | 4.30% | 34.71% | 5.99% | 49.43% |
| Boston | 10.27% | 26.98% | 17.04% | 37.76% |

At 4 m, DeepForest precision/recall were 76.52%/4.87% for SF and 63.20%/14.41%
for Boston. Swin was 84.05%/44.81% and 82.75%/33.24%, respectively. Recall is
the principal gap, not merely a flood of obviously invalid detections.

Best **observed** F2 over the prespecified 0.05–0.90 validation threshold grid:

| City | DeepForest crown-aware | Swin crown-aware | DeepForest 4 m | Swin 4 m |
|---|---:|---:|---:|---:|
| SF | 11.42% | 44.15% | 17.99% | 65.73% |
| Boston | 15.32% | 45.53% | 26.60% | 69.22% |

These maxima occur at DeepForest 0.05 and Swin 0.20. DeepForest peaks at the
export floor, so its true optimum below 0.05 is unknown. These are exploratory
validation-selected scores, not replacement headline/holdout scores. A high
goal-aligned F2 does not certify that ignored predictions are real trees—lawns
in protected vegetation remain a known limitation. No AP claim is made.

Conclusion: the locally adapted model beats this **out-of-the-box pretrained
release on our urban NAIP point-detection task**. This does not demonstrate
state-of-the-art crown segmentation, superiority over locally fine-tuned
DeepForest, or unbiased generalization to new cities. Further resolution/
tiling tuning and pretrained-model comparisons would be separate experiments.

Artifacts: `runs/sf-boston-deepforest-neon-v1/goal-curves.json`, `baseline.json`,
and per-city evaluation bundles with raw boxes, predictions, frozen truth,
matches, coverage manifests, and smoke PNGs. No training-chip inference.

Run: `sf-boston-deepforest-neon-v1` (inference only; no optimization).
Reference cohort: `sf-boston-naip-swin-v2-finetune`, both validation cities.
No live curation changes, no test inference, no training-chip inference.

## Reproducible reference

- Official `weecology/deepforest-tree` NEON checkpoint at Hugging Face revision
  `cc21436bc5d572dde8ff5f93c1e71a32f563cace`; checkpoint SHA recorded at runtime.
- DeepForest v1.5.0 reference RetinaNet/ResNet50-FPN construction in torchvision;
  strict complete state-dict loading. No random unmatched heads allowed.
- Raw RGB channels in [0,1]; stock torchvision normalization/internal resizing;
  original chip pixel coordinates preserved on output. NIR unused.
- NMS IoU 0.05; inference score floor 0.05 (lowered from release default 0.1
  to retain the diagnostic confidence sweep); stock 300 detections/image cap.
- Native crown boxes retained. Centers for point matching; equivalent-ellipse
  crown radius from box width/height explicitly marked as derived geometry.
- No species, genus, or DBH predictions: null, not zero or invented classes.

## Comparison contract

Reuse the exact prepared imagery and copy the reference ground-truth parquet;
sample the same frozen center/detection masks at predicted centers. Complete
coverage includes zero-detection chips. Goal-aligned scoring uses one-to-one
matches and ignores unmatched zero-mask predictions rather than crediting them.
Report fixed 2 m/4 m and target-crown-aware matching, plus threshold curves for
both DeepForest and Swin on this same cohort. Curves on validation are
exploratory; a best validation threshold is not a new unbiased holdout result.

This is a **published pretrained baseline**, not a claim that DeepForest is
the strongest current method or that an urban point-detection comparison is
equivalent to a public crown-segmentation benchmark. Bounding-box center error,
NAIP resolution, canopy density, and RGB versus RGBN can all affect the result.

Three adapter tests pass. Detection-only UI test passes; broader model-debug
tests have one pre-existing stale `unreviewed` checkbox expectation (the UI now
uses a review-status selector). Reviewer code changes require a coordinated
restart; no live reviewer restart was performed for this experiment.

Sources:
- https://huggingface.co/weecology/deepforest-tree
- https://github.com/weecology/DeepForest/blob/v1.5.0/src/deepforest/models/retinanet.py
- https://github.com/weecology/DeepForest/blob/v1.5.0/src/deepforest/data/deepforest_config.yml
