# v20 fine-tune

## Failed attempt

The allocated A10 failed during training with CUDA's `uncorrectable ECC error`.
Supervisor exit code 1. Failure artifacts were downloaded and hash verified,
teardown acknowledged, and Lambda returned no active instances at the completion
check. No completed evaluation or F2 uplift is available for this attempt.
Frozen inputs remain available for a retry; do not refreeze ongoing curation.

## User-requested fresh-curation retry

User subsequently requested repulling latest curation. Both cities were published
again and a new verified freeze created for `sf-boston-naip-curation-v20-retry1`.
The failed run is untouched. Retry warm-start remains v19 `012.ckpt`.
New exact instance: `91e13a1a5bd04141820cbfe304ae9659`.
Local preparation used isolated dependencies (pyproj 3.7.2 matching the reviewer)
because the existing .venv's pyproj import was broken. No reviewer restart or
local training was performed. Monitor verifies startup, collection and teardown.

Run: `sf-boston-naip-curation-v20-finetune`.

Both cities published and frozen before allocation. Relative to v19, SF has
2,783 changed projected tree reviews and 594 regions (previously 335); Boston
has 1,803 changed projected reviews and 2,071 regions (previously 1,743).
Projection counts are not unique trees or necessarily changed training targets.

Warm start: v19 `012.ckpt`, weights only, fresh optimizer, learning rate 0.00003,
40 epoch cap with validation early stopping. Crown head and inherited size-aware
center policy retained. Test remains sealed.

Frozen inputs: `artifacts/run-inputs/sf-boston-naip-curation-v20-finetune/`.
Instance receipt: `artifacts/sf-boston-naip-curation-v20-finetune-instance.json`.
Exact instance: `981239301da741c49007aafdeb1eacc4`.
Launcher installs supervised termination and independent deadline; heartbeat
monitor verifies startup, collection, goal-aligned reporting and teardown.
