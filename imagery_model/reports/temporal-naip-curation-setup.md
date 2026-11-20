# Multi-year Boston/SF imagery preparation

The November 12 setup adds separate acquisition identities for Boston 2021/2025
and San Francisco 2020/2024, alongside the existing Boston 2023/SF 2022 images.
The official Planetary Computer NAIP catalog was queried directly. Boston 2025
also returned geometrically overlapping Maine-labeled assets; these were excluded.
Only Massachusetts assets for Boston and California assets for SF are acquired.
Each additional pass covers the eight existing NAIP source footprints for its city.

Tools in the active checkout:

- `lambda/discover_temporal_naip.py` writes an independent catalog snapshot.
- `lambda/acquire_temporal_naip.py` downloads and hashes RGBN assets sequentially,
  without changing the original imagery indexes or persisting signed URLs.
- `lambda/prepare_temporal_naip.py` creates VRTs on the original city's exact grid,
  renders separate review workspaces, omits scenes with less than 98% nonzero
  coverage, and registers completed acquisitions in
  `artifacts/qa/temporal-acquisitions.json`.

The per-acquisition configuration, receipt and READY marker live under
`artifacts/run-inputs/<city>-naip-<year>/`; review scenes live under
`artifacts/qa/registration/<city>-naip-<year>/`. Original review files are untouched.
The registry is loaded at curator startup. Its **Imagery acquisition** selector
opens the corresponding registration gallery, independently of model predictions.
These inputs do not yet have new-model prediction overlays.

**Populate from peer image** previews human-curated trees from the nearest dated
acquisition with overlapping curation and the same geographic split policy. Ties
prefer the older acquisition. Accepting the preview copies centers, available
human crown sizes and explicit species corrections through normal review saves.
Existing decisions and nearby confirmed trees are preserved. Repeating the action
does not create duplicates. Background/group polygons, predictions, and completion
flags are not copied. Copying clears the destination scene's completion flags so
the user can check changed tree presence and crown sizes before publishing.

Geographic splits are shared across years, not randomly redrawn. Scene membership
is attached from the reference v20 materialized manifest where available. Ambiguous
or held-out scenes cannot use peer copy; conflicting point splits fail closed.
The registry records source imagery hashes and the shared grid/split signature.
Dates used to choose peers have year precision; actual per-source acquisition
dates remain in the acquisition receipt.

Training integration is deliberately staged: new configurations carry an
`imagery.acquisition_id`, and chip building currently rejects them until a frozen
curated-admission adapter is supplied. Downloading or opening imagery must not
silently admit municipal inventory or copied suggestions into training. The next
integration step is freezing published per-acquisition supervision, admitting any
human-curated train tile with trusted masks and only full-tile more_done validation,
then combining acquisition-qualified chip IDs while preserving the true city.
No new training or GPU allocation is part of this setup.

Validation so far: 23 focused tests passed, including HTTP isolation between two
acquisitions, peer chronology, duplicate/overwrite protection, split guards,
existing polygon completion and cross-city assignment behavior. A real browser
smoke test in an isolated workspace copied 36 Boston trees from 2023 to 2021,
saved and reloaded them, reported zero additional copies on repeat, and kept
completion flags unset. The live curator and original annotations were untouched.

## Prepared inputs verified

| Acquisition | Additional review tiles |
|---|---:|
| Boston 2021 | 713 |
| Boston 2025 | 695 |
| San Francisco 2020 | 876 |
| San Francisco 2024 | 876 |
| Total | 3,160 |

All four passes finished downloading and preparing. Independent checks verified
exact reference CRS, grid transform, dimensions and four-band layout, each scene
image, no test scenes, and empty initial curation. The registry contains these four
passes plus the two originals. Final focused routing, peer-copy, admission and
promotion tests passed (16 tests). The live curator is healthy at port 8765;
the user confirmed saved edits and authorized restart. Restart completed; all four
new acquisition pages, assets, peer buttons and isolated empty annotation states
were verified on port 8765. See live-activation.json in the QA scratch directory.

The new imagery remains unavailable to training until the frozen curated-source
adapter is completed. This is a preparation/curation milestone, not a claim that
temporal training or validation has been run.

## Normal chip-review integration

User clarified that acquisition selection belongs in the normal model chip-review
grid, with peer copying inside its existing tile editor. Normal review now filters
cohorts by acquisition, preserves the year through train/validation navigation,
and offers an imagery selector under the same model. Cards/editor titles display
city, year and the unchanged geographic chip ID. Existing annotation workspaces
remain separate per acquisition. Temporal validation queues use their own actual
chip manifest instead of inheriting the original-year validation receipt.

The curator was reloaded and the live Boston 2025 editor/title/peer button verified.
Fifteen focused tests pass, including same-chip/different-year isolation and model
grid provenance checks. Additional prediction passes are currently running locally
for Boston 2025 and SF 2024 with promoted v20 checkpoint 001-0000569.ckpt, using
two CPU cores at BelowNormal priority. The first Boston plan has 520 eligible
standard development chips (432 train, 88 validation); shifted/off-grid scenes and
scenes outside the frozen reference development manifest are excluded. These are
unlabeled predictions: no invented miss/false-positive metrics or training labels.
New acquisition-qualified evaluation directories are appended; original run
selection, reports and evaluations are preserved. Prediction completion/coverage
receipts are required before describing these cohorts as ready.

## All imagery

All imagery is now available in normal chip review. It combines the selected
model/cities across available acquisition cohorts while retaining separate keys,
review status and edits per year. Mixed-year queues retain each tile's acquisition
and prediction cohort in both directions. The user authorized a saved-edits
restart; the live Boston view and same-chip 2023/2025 queue navigation were
verified in the browser, with no console errors. Sixteen focused tests passed.
Boston 2025 predictions are complete; SF 2024 processing is still running.

## Prediction and UI completion

Both CPU passes completed with exit0: Boston2025 520tiles (432train/88validation),
SF2024 676tiles (456train/220validation). Independent coverage and empty-label/zero-
supervision checks passed for all four cohorts. These are review predictions, not
new training. Completion audit is prediction-completion-audit.json in the QA scratch.
The redundant registration acquisition header was removed; the title keeps city/year.
Google Street View is the default camera provider; Mapillary remains available.
Saved-edits restart authorized and completed; live browser checks passed.


# City inventory baseline for temporal review (2026-11-12 follow-up)

User clarified that each new imagery year starts with the normal underlying city inventory reference points, not an automatic copy of prior-year curated labels. Prepared city-review-references/<v20>/<cohort> companions for all four temporal prediction cohorts, projected from frozen run-inputs city inventory with standard split eligibility, planting-date filtering, output-cell quantization and collision exclusion. Original evaluations/prediction masks/curation untouched; prior exploratory review-references companions are superseded and unused (SUPERSEDED.json). Ranking and overlays now read city inventory companions; they do not confer human review or training admission. At .13/2m Boston2025 has 500/520 tiles with missed inventory references; SF2024 674/676. Audit qa/temporal-naip-20261112/city-reference-ranking-audit.json. Peer import now preserves explicit per-tree not-tree/duplicate/uncertain/occluded decisions as well as corrections, with peer-image:<acquisition> provenance; preserves existing destination edits and never copies completion flags or background polygons. 33 focused tests and generated UI JS syntax passed; real four-cohort native/Python rankings agree.

NOT LIVE YET: user explicitly answered "Wait, I'm still editing" to restart request. Do not restart/reload their current tab until fresh saved-edits confirmation. Earlier browser navigation was cancelled at beforeunload; no edits discarded. Server remains running previous code. Next: after saved-edits approval restart via curator.py and verify All imagery ranking/card references/import UI. No GPU or new inference needed. Temporal training admission adapter remains unfinished.



# Temporal inventory references live (2026-11-12)

User confirmed saved edits and authorized restart. Curator stopped cleanly, restarted through curator.py, ready on8765 PID41304. Live HTTP verified all four new-year ranking cohorts: Boston2025 train432/24249inventoryreferences/412tileswithmisses, validation88/7820/88; SF2024 train456/30846/456, validation220/9929/218 at .13/2m. Separate browser tab verified All imagery + most missed shows Boston2025 cards with numeric inventory-based missed counts at top; original user tab untouched, test tab closed. No curation writes/imports performed. Audit qa/temporal-naip-20261112/city-reference-live-audit.json. This supersedes the pending-restart status below. Future restarts require fresh saved-edits confirmation. Temporal training adapter still unfinished; no new inference or GPU.



# Peer areas/import-state repair LIVE (2026-11-12)

User explicitly authorized copying background/woods/single-tree areas, restarting after implementation, and repairing Boston2025 scene-074 / r000102_c000037. Added confirmed-background/protect/confirmed-woods/single-tree import from nearest same-city/split-policy peer, projected and clipped to target development tile, deterministic IDs, idempotence, editable source-year tags and scoped revert. Freehand crossed polygon lobes retained with linework repair before clipping; no completion, notes/observations, vegetation groups or prediction layers copied. These supersede older area-exclusion notes below. 24 focused tests passed including polygons/circles, clipping, area-only source, repeat import, revert preserving later edits, crossed lobes, and overlap provenance; generated editor JS syntax passed.

Curator restarted ready8765 PID37700, exact user page reloaded and new Revert imported curation control verified. Executed user-authorized normal UI peer import:500 missing crown-overlap occlusion decisions,0newtrees,0areas on this particular tile. Saved status verified. Independent canonical-file comparison confirms EXACTLY500 tree records changed, existing30 added trees and all other regions unchanged; repeat live preview copies0. Repair receipt qa/temporal-naip-20261112/peer-import-fix/live-repair-audit.json; before-file backup retained. Source2023 untouched. Current tile now reflects all770 source tree decisions. No further restart approval pending; future restarts need fresh saved-edits confirmation. No GPU/training changes.

