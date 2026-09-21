# v21 fine-tune launch

## Completed results

Training finished after 14 epochs; the selected checkpoint is `001.ckpt` (epoch 2). Results were downloaded and hash-verified, collection acknowledged, and Lambda returned no active instances. The A10 is terminated.

Goal-aligned F2 at confidence 0.35, using each run's own frozen validation labels and masks:

| City | Matching policy | v20-retry1 | v21 | Delta |
|---|---|---:|---:|---:|
| SF | Crown-aware, 2–4 m | 34.84% | 34.51% | -0.33 pp |
| Boston | Crown-aware, 2–4 m | 25.11% | 26.03% | +0.93 pp |
| SF | Fixed 4 m | 49.89% | 49.62% | -0.27 pp |
| Boston | Fixed 4 m | 35.30% | 36.24% | +0.94 pp |

Unmatched predictions in ignored loss-mask regions do not count as false positives in these metrics. These are end-to-end native-label comparisons, not isolated model uplift: Boston validation targets changed from 11,472 to 10,813; SF remains 12,659. Count equality alone does not prove label/mask equality. Goal-aware metrics: `artifacts/benchmarks/v21-native-goal/metrics.json`. Separate common-label inventory-relative diagnostics: `artifacts/benchmarks/v21-v20retry1-paired/` (not goal-aligned loss-mask decomposition). No sealed test evaluation.

- Run: `sf-boston-naip-curation-v21-finetune`.
- Parent: `sf-boston-naip-curation-v20-retry1`, best checkpoint, fresh optimizer.
- Latest saved SF and Boston curation was published and frozen before allocation.
- Crown head and inherited size-aware center policy retained; 40-epoch cap, learning rate 3e-5.
- Exact instance: `1a72298973ce4d86a02dd545e4ee9b52` (A10).
- Supervised deployment and automatic teardown installed. Result collector started.
- Startup CUDA focal-loss and crown-head forward/backward checks passed; dataset preparation underway at handoff.
- No new scores yet. Compare goal-aligned results to v20-retry1, distinguishing label drift from model improvement; test remains sealed.

## Attribution diagnostic

The common-chip, common-label **inventory-relative** diagnostic shows SF label effect 0.00 pp and model effect -0.27 pp at both fixed radii. Boston's label/model effects are +1.09/-0.35 pp at 2 m and +1.49/-0.82 pp at 4 m. Thus Boston's native-score improvement should not be described as isolated model improvement: cleaned validation labels explain the positive direction in this diagnostic. These effects are not an exact decomposition of the goal-aligned scores above, since this diagnostic does not reproject loss-free masks.

## Separate reviewer changes

Added crowns now display stable compact `A###` center badges (visual labels, not database identities). Full UUIDs remain the persistent identity; compact badges can collide.

In full-screen curation, select an inventory tree or click an added crown, press Ctrl+C/Cmd+C, hover the destination on the image, then Ctrl+V/Cmd+V. Each paste creates a fresh manual crown with copied species and radius. Inventory IDs, DBH, planting date and exclusion/review status are not copied. The clipboard is in-page only; navigation/reload clears it. Normal text editing is unaffected. These UI changes do not alter the frozen training inputs and require a reviewer restart.
