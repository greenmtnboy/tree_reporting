# V21 curated fine-tune: deployment failure, teardown verified

The November 15 attempt failed during installation of frozen imagery, before GPU preflight, data preparation or optimization. No new checkpoint or quality comparison exists. The exact allocated instance is API-verified terminated with null IP. Promoted v20 checkpoint `001-0000569.ckpt` remains the training parent; live curator defaults are unchanged.

## Included curation and frozen preparation

The user approved publication of Boston 2021's 24 saved background tiles and saved NYC edits. Both were published and included. A newer saved Fairfax area was also included in the latest-curation snapshot. Publication did not edit saved annotation content.

- Boston 2021: 24 admitted training tiles, all tree-free; no validation cohort.
- Boston 2025: 30 training and 10 fully reviewed validation tiles, 1,784 combined retained targets.
- Fairfax local preflight: 9 training tiles / 179 targets; 5 validation tiles / 122 targets.
- NYC local preflight: 7 training tiles / 731 targets; the original 19 pinned validation image arrays remain unchanged before the full-review admission filter.
- SF/Boston/NYC frozen reviewed-ID counts are not actual materialized validation counts. Remote native-city materialization did not run.
- Partial training tiles supervise only trusted published curation. Validation remains explicit full-tile More Done. Sealed tests were not evaluated.

Temporal admission was checked on Windows and locked Linux, including background-only tiles. A subpixel boundary spillover was corrected before sealing; all 64 retained new-year tile arrays were byte-equivalent to the corresponding initial materialization. Prelaunch validation comprised 55 unique focused tests and an offline lock check.

## Failure and teardown

Run: `sf-boston-nyc-fairfax-naip-swin-30cm-v21-curated-finetune`.

Exactly one A10 instance was allocated: `74c67861232544d088c9f751e0882137` in us-east-1. Allocation epoch: `1794777062.8258924`. Absolute lifetime cutoff: November 16 01:11:02 UTC / November 15 8:11:02pm Eastern. Optimization cutoff was one hour earlier.

Deployment session 16525 exited 1. Source and input upload hashes passed, and the persistent root write/read probe passed. Installation then raised:

```text
PermissionError: [Errno 13] Permission denied:
/lambda/nfs/tree-reporting-dev/urban-tree-ml/imagery/usbos/2021
```

The root-directory storage probe did not establish permission to create a year beneath the existing city directory. The installer ran unprivileged, while prior Docker work can leave imagery directories owned by root. The precise remote directory ownership was not queried after termination began; the observed permission failure and a matching root-owned-directory Linux reproduction establish the deployment gap.

The deployment exception handler requested termination. Subsequent exact API checks verified `terminated`, IP null. No collector was started because deployment never completed; there is no COMPLETE marker, training result, GPU preflight receipt or result archive for this attempt. Any files installed before the failure remain on persistent storage; no recovery allocation was made. Provider billing duration is not available and is not inferred from local timestamps.

## Local corrections, ready for a separately authorized attempt

The launcher now uploads the small asset receipt and checked installer first. It uses noninteractive `sudo -n` for the existing path-restricted installer, probes actual destination ancestors before uploading the large archive, verifies the uploaded archive hash, and still refuses differing existing artifacts. It does not chmod or recursively change ownership of shared storage.

An independent review also found that the inherited internal optimization deadline callback applied only to ImageNet capacity jobs. An external cutoff guard was installed on this attempt, without editing its frozen source, but was never needed before termination. Future warm-start runs now use the internal deadline callback too, retain the true final checkpoint and selection guard, and explicitly report `wall_clock_training_cutoff` when applicable.

Validation after these corrections: 30 focused tests passed, including real CPU warm-start optimization that stops at the deadline and preserves final/selected states. A Linux permission reproduction verified unprivileged failure before upload and successful bounded privileged installation with identical payload bytes. Five mocked external-guard behavior checks passed.

Original v21 archives were preserved and not repacked after failure. The local fixes therefore do not alter the failed attempt's provenance. A retry requires fresh user authorization, a distinct attempt/output path, a new reviewed source bundle with the corrections, the same frozen curation semantics, and another one-instance/four-hour cap. It must not blindly redeploy into the existing run-input directory.

## Evidence

Shared artifacts:

- `<run>-failure-audit.json`: exact final API state, failure, parent and authorization boundary.
- `<run>-local-prelaunch-audit.json`: preparation and sealed archive hashes.
- `qa/curation-admission-v21/deploy.log`: verified uploads and exact-instance termination request.
- `qa/curation-admission-v21/deployment-fix-tests.log`: 30 passing focused tests.
- `qa/curation-admission-v21/optimization-guard-install.json`: independently verified external cutoff.
- Publication, retained-array, boundary admission and Linux admission receipts in the same QA directory.

Source SHA256: `56d5dee596addaf8ccb9a868160aeeea500e3aea6be6ec50447faeb5570b7fd5`.
Inputs SHA256: `3dd3f1b756a087c4b2edd213234145b929b75fa540469ff9453a0747f6d432d4`.
Parent SHA256: `ab428f589573be7666f840d467f672ad3144b50a948a8b621cc6eb0ceec86c9d`.
