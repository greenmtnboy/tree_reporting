# Fairfax County tree prediction data handoff

Prepared 6 November 2026. The requested outcome is the normal tree prediction and curation workflow for Fairfax County, Virginia, with nearby Northern Virginia inventories available for species calibration.

## Ownership and first deliverable

The data agent owns source discovery, jurisdiction boundaries, inventory ingestion, species normalization, provenance, and eventual public data-pipeline registration. The model agent owns NAIP discovery and acquisition, tile sampling, prediction preparation, curator integration, and model evaluation.

Start by delivering a local source bundle. This lets model preparation proceed while public pipeline registration is still being completed. An unavailable county-wide inventory should be documented, and the boundary and available calibration inventories can still be delivered.

Repository is `C:/Users/ethan/coding_projects/sf_tree_reporting`. Read its `AGENTS.md`, `EXTENDING.md`, `docs/DATA_PIPELINE.md`, and `docs/SPECIES_ENRICHMENT.md`. The active imagery implementation is `C:/Users/ethan/AppData/Local/Temp/sf-tree-review-done/imagery_model`. Artifacts are shared at `C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts`.

Use the provisional code `USFFX` for Fairfax County, subject to checking the city registry and coordinating any correction in the bundle. Preserve Fairfax City, Arlington County, Alexandria, and other jurisdictions under their own identities. Fairfax City is an independent jurisdiction.

## Source bundle contract

Write a versioned directory under `imagery_model/artifacts/run-inputs/fairfax-source-v1/`. Paths in its manifest are relative to that directory. The model CLI accepts a different bundle directory explicitly.

```json
{
  "schema_version": 1,
  "city": "USFFX",
  "jurisdiction": "Fairfax County, Virginia",
  "boundary": "boundary.geojson",
  "inventory": "inventory.parquet",
  "calibration_inventory": "calibration-inventory.parquet",
  "sources": [
    {
      "name": "Publisher and dataset name",
      "url": "https://publisher.example/dataset",
      "retrieved_at": "ISO-8601 UTC timestamp",
      "jurisdiction": "Publisher's jurisdiction",
      "license": "Terms or license URL",
      "coverage": "Street trees, parks, public land, sampled sites, etc."
    }
  ],
  "limitations": ["Document inventory completeness, dates, and unresolved mappings"],
  "sha256": {
    "boundary.geojson": "SHA256 of exact delivered file",
    "inventory.parquet": "SHA256 of exact delivered file",
    "calibration-inventory.parquet": "SHA256 of exact delivered file"
  }
}
```

`boundary.geojson` must be a valid WGS84 Polygon/MultiPolygon geometry, Feature, or FeatureCollection describing the county's actual territory. Preserve holes and jurisdiction exclusions. Record the authoritative boundary source and retrieval date.

`inventory.parquet` contains Fairfax County records. `calibration-inventory.parquet` optionally contains nearby jurisdictions. Either inventory entry may be JSON `null` when unavailable. The model pilot can sample imagery with the boundary alone. Include hashes only for delivered files.

Both inventory files use the existing imagery-reader columns below. Additional provenance columns are welcome.

| Column | Contract |
|---|---|
| `tree_id` | Stable nonempty string, unique within each source/jurisdiction. Preserve source IDs and namespace them where needed. |
| `city` | Uppercase jurisdiction code. County inventory rows use the manifest's `city`. |
| `data_source` | Traceable source label. |
| `species` | Centrally normalized scientific name or existing supported unknown/genus sentinel. Preserve original names in a separate column. |
| `plant_date` | Actual planting date when known, otherwise null. |
| `diameter_at_breast_height` | DBH in inches, otherwise null. Record original units and conversion. |
| `latitude`, `longitude` | Finite WGS84 coordinates. |

Keep source survey/update dates separately from planting dates. Record inactive/dead/stump/empty-site filtering and counts. Use `data/raw/shared/ingest.py` normalization and schema helpers. Validate coded domains before translating common names. Preserve genus-only information when a species-level identification is unavailable. For ambiguous cultivar/hybrid names, report the unresolved mapping.

Deliver `source-audit.md` with row counts before/after filtering, ID uniqueness, coordinate checks, date availability, DBH units, species counts, jurisdiction coverage, acquisition terms, and unresolved limitations. Keep source snapshots and reproducible extraction code. Check duplication across overlapping sources using the established ingest/dedup workflow.

## Verified starting points

- Arlington has a public point inventory with common names, DBH, installation dates, inactive status, and other attributes. On 6 November 2026, `Inactive = 0` returned 30,910 records. Five common-name groups absent from the current model species vocabulary accounted for 5,523 records. Audit the domain/name mapping and snapshot freshness before using this figure. [Arlington DPR Trees](https://arlgis.arlingtonva.us/arcgis/rest/services/Open_Data/od_DPR_Tree_Points/FeatureServer/0)
- Fairfax City publishes an inventory summary reporting 11,487 trees and 146 species/cultivar classifications in its 2023 survey. Locate an authorized georeferenced export and its terms. [Fairfax City inventory summary](https://www.fairfaxva.gov/files/assets/city/v/1/sustainability/documents/tree-inventory-summary-2023.pdf)
  - Interim, licence unclear: a George Mason University ArcGIS Online account republishes the inventory as `FairfaxTrees_WFL1` layer 10 (13,155 points with scientific and common names, DBH, status and native status, last edited 11 December 2025). The City did not publish it, and the item states no licence. Bundle v2 includes it as calibration source `USFXC` / `FAIRFAX_CITY_GMU_COPY`, and every row carries `source_license_status = 'unclear_pending_official_source'`. Use it for calibration only and never publish it. Replace it once the City supplies an official export. [FairfaxTrees_WFL1](https://services5.arcgis.com/fxnFqGOVRzV4g6ac/arcgis/rest/services/FairfaxTrees_WFL1/FeatureServer/10)
- The Fairfax County ArcGIS service named `Trees2` returned just two records when checked. Its name alone gives no evidence of county-wide coverage. [County Trees2](https://services1.arcgis.com/ioennV6PpG5Xodq0/arcgis/rest/services/Trees2/FeatureServer/0)
- Alexandria's `alxPublicParksWm` tree layer is labeled **Notable Trees**. Record this selection bias if it is used for calibration. [Alexandria parks layers](https://maps.alexandriava.gov/arcgis/rest/services/alxPublicParksWm/MapServer/layers)

Inventory completeness must be explicit. Missing inventory points on residential or private land leave tree presence unknown. The model workflow will admit complete validation labels through full-tile curation.

## Imagery coordination

The model agent is querying Planetary Computer NAIP metadata and will choose a frozen acquisition after inspecting coverage. The Fairfax-region rectangle searched on 6 November returned 65 Virginia items in 2023, all at native 0.6 m and acquired in October/November. Nearby Maryland items in the same rectangular search have different dates/resolutions. Central Fairfax also has September 2021 coverage. County clipping and image inspection will determine the final selection.

Supply the official boundary and source inventory dates early. The model agent will retain RGBN, 512-pixel tiles at model resolution 0.3 m, original source-resolution metadata, and geographic train/validation/test blocks.

## Public pipeline work

Once jurisdiction codes and viable sources are established, use `data/raw/tools/new_city.py` and the `EXTENDING.md` runbook for registration. Inspect its dry run first. Implement the source adapter and freshness probe, then the standard OSM/landmark/dedup/position-policy wiring for each jurisdiction being added. Keep the model source bundle available independently of public pipeline completion.

Run the applicable ingest and city-wiring tests. A Trilogy dry run for each new city target must report exactly one asset. Report the chosen codes, modified files, checks run, and local bundle path back to the model agent. Publication/deployment can follow review of the prepared changes.

## Model-side entry points

The active checkout's `lambda/prepare_fairfax_pilot.py` exposes metadata discovery, bundle auditing, and sample preparation. Its `--help` describes the current commands. `fairfax_pilot.py` consumes the bundle contract above. Species coverage is measured against the retained v17 taxonomy, and initial samples use existing model weights.

Run the bundle audit from PowerShell after delivery. Use a fresh output filename on a subsequent audit to preserve the previous receipt.

```powershell
$active = 'C:/Users/ethan/AppData/Local/Temp/sf-tree-review-done/imagery_model'
$artifacts = 'C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts'
$python = 'C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/.venv/Scripts/python.exe'
$env:PYTHONPATH = "$active/src"
& $python "$active/lambda/prepare_fairfax_pilot.py" audit `
  --bundle "$artifacts/run-inputs/fairfax-source-v1" `
  --artifacts $artifacts `
  --output "$artifacts/run-inputs/fairfax-source-v1/model-source-audit.json"
```

The discovery snapshot is `artifacts/run-inputs/fairfax-pilot-discovery-20261106/catalog.json`. It contains 178 candidate items across 2021 and 2023, including 130 Virginia items. These are rectangular search results awaiting county clipping and acquisition selection.

Model preparation accepts a native 60 cm source mosaic VRT and its latest actual acquisition day. It verifies the retained checkpoint, derives a 30 cm RGBN view, and defaults to 80 train-area and 40 validation-area tiles through seeded geographic-block sampling. Whole tiles must fit inside the county boundary and the guarded interior of their assigned block. Test blocks are excluded. Inputs with missing imagery are rejected or skipped during selection. Prepared tiles carry unavailable labels and zero detection supervision until curation. Prediction inference and explicit curator registration follow preparation.

The `acquire` command consumes the delivered bundle, saved catalog, one year, and explicit item IDs. It checks Virginia/native-60cm/RGBN metadata and county intersection, downloads through the existing imagery validators, verifies file checksums, and builds the source mosaic. Identical interrupted requests can resume in their acquisition directory. The `predict --pilot <prepared-directory>` command verifies prepared input/checkpoint hashes and runs both review cohorts on CPU into an isolated output directory.

Two matching-location 2021/2023 RGBN crops near Reston have passed single-tile retained-v17 CPU inference checks. Receipts are under `artifacts/run-inputs/fairfax-pilot-discovery-20261106/season-previews/`. These acquisition checks use a reserved train block and have unavailable labels.

## Delivered pilot, 7 November 2026

The source bundle at `artifacts/run-inputs/fairfax-source-v1/` passed the independent model audit. It includes the official county boundary and nearby public inventories for calibration. County-wide inventory coverage remains unavailable. Species vocabulary support among named records is 56.18% for Arlington and 52.12% for Falls Church. These figures describe class coverage in those inventories. Accuracy requires curated labels.

The model-side pilot uses four geographically distributed Virginia NAIP scenes acquired on 11 October 2023, one selected near each county quadrant's representative point. Acquisition receipts and explicit scene IDs are in `artifacts/run-inputs/fairfax-pilot-2023-acquisition-v1/`. Native 60 cm RGBN imagery is resampled to the existing 30 cm model grid.

Preparation and retained-v17 CPU inference completed for 120 tiles on 120 distinct geographic blocks, with 80 train-area tiles and 40 validation-area tiles. The pilot is at `artifacts/run-inputs/fairfax-pilot-2023-review-v1/`. Its `verification-receipt.json` records input/checkpoint hashes, full-tile county containment, split/block checks, zero training supervision, and exact inference coverage. Predictions contain 4,798 candidate peaks at confidence 0.13. Accuracy is unmeasured pending curation. Explicit registration in the live curator remains pending.

The handoff is complete when the model agent can validate the delivered boundary and any available inventories using the audit command, and can trace every normalized record to its source. The overall work concerns tree predictions, species coverage, and curation.

## Source bundle v2, 8 November 2026

`artifacts/run-inputs/fairfax-source-v2/` adds Fairfax City (`USFXC`, 13,155 rows) to the calibration inventory from the third-party copy described above. Its licence is unclear, so a consumer should filter on `source_license_status` before using the rows anywhere they could be republished. 96 of its rows fall inside the county boundary along the shared line. The model audit passes; 56.57% of named Fairfax City rows are in the v17 vocabulary. v2 also changes how the county boundary is repaired. In November 2026 the county service began encoding its three rings as overlapping MultiPolygon shells, which `make_valid` read as filling Fairfax City. The script now rebuilds the territory even-odd from the rings, and the result is identical to v1's.
