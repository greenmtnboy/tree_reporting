# v17: Boston curation fine-tune

Experiment: `sf-boston-naip-curation-v17-finetune`.
Parent: `sf-boston-naip-curation-v16-crown-center`, best `009.ckpt`.

Fresh published SF/Boston snapshots frozen on 2026-09-17. SF is unchanged;
Boston has 1,901 changed expanded review records (1,897 newly explicit), and
1,375 region records versus 722 in v16. These are annotation-record counts,
not counts of retained model targets.

Weights-only warm start, fresh optimizer, learning rate 0.00003, maximum 40
epochs, existing validation-loss early stopping. Crown head stays enabled;
the parent's `center-policy.json` is inherited explicitly. Test stays sealed.

Crown-size-aware goal-aligned scoring is the ongoing evaluation policy, not
a commitment to keep v16 as the permanent model. Also retain fixed 2/4 m
diagnostics. Unlike v16's controlled comparison, labels may differ here:
report native scores plus paired/common-label analysis, and separate label
changes from model changes. Do not run the identical-label comparator without
first checking its precondition.

Results collector must verify the downloaded archive and acknowledge teardown.
The remote supervisor and independent deadline protect against unattended
compute. Monitor automation: `finish-sf-boston-curation-training`.

## History UI

The shared header is the sole live history city selector. `city=all` renders
separate labeled graphs. Table deltas remain city/threshold-specific. Foreign
saved dataset filters are cleared on entry so switching cities cannot silently
empty the graph. Tested in Node and the live browser for Boston, SF, and All.
