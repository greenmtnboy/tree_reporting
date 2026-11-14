# Current heat and water sources per city

Environmental context follows the tree-data publication model: stable GCS paths
containing the latest normalized source observations. `v1` is the schema version,
not a poster revision. No tree copies, crown caches, poster aggregates or replay
bundles are published. Binaries stay out of Git. Consumers combine these sources
with current trees and apply their own spatial and temporal analysis rules.

## Public Boston assets

All paths are under `gs://trilogy_public_models/duckdb/context/`:

| Object | Contract |
| --- | --- |
| `usbos_surface_temperature_v1.parquet` | Valid 30 m delivered-grid pixel/date observations for the latest completed June-August season. |
| `usbos_water_v1.parquet` | Current intersecting water features, GeoParquet 1.1 with WKB geometry in EPSG:4326; WKT also exposed for Trilogy spatial queries. |
| `usbos_context_v1.json` | Extent, source links, acquisition dates, per-date usable coverage, QA rules, resolution, units, timestamps, hashes, lengths and uploaded object generations. |

Public base URL: `https://storage.googleapis.com/trilogy_public_models/duckdb/context/`.
The gallery should link this city's stable metadata URL after the first seed is
verified. It should not present current data as the exact inputs to an older image.

Heat row grain is `observation_id`: city + acquisition date + projected 30 m
cell ID. Columns include `cell_id`, `city`, `acquired_on`, `easting_m`,
`northing_m`, `longitude`, `latitude`, `surface_temperature_c`,
`valid_scene_count` and `source`. Same-date scenes are mosaicked by averaging
valid measurements. Every usable acquisition date is retained; there is no
poster-specific date selection or cross-date average. Missing rows mean missing
or rejected measurements, never zero. The metadata describes the entire grid,
including dates with no valid pixels. Grid coverage is not land-area coverage.

Water grain is `feature_id`, with `city`, `source`, `source_feature_id`, nullable
`name`, `water_class`, `geometry` and `geometry_wkt`. Full source polygons are
retained without display simplification; clip/union for the caller's area analysis.
Invalid geometries are repaired and empty/non-polygon results rejected.

## Sources and refresh

`data/context/config.py` declares the Boston metro coverage rectangle and source
choices. The adapters in `sources.py` fetch live source catalogs/features each
build; they do not depend on rendering caches. Scratch scene arrays exist only
during the build. The current adapters support Landsat 8/9 C2L2 and MassGIS water;
new cities require explicit bounds, projection and suitable provider adapters.

- Heat: USGS Landsat Collection 2 Level-2 via Microsoft Planetary Computer.
  Latest completed June-August season: before September, use the previous year;
  from September, use the current year. No automatic fallback if that year's
  sources are unavailable. An explicit `--year` supports local investigations;
  publication refuses to replace a newer year with an older one.
- Temperature is clear-sky, late-morning **surface**, not air, temperature.
  Thermal sampling is about 100 m despite the delivered 30 m grid. Cloud,
  cirrus, shadow, snow, water and medium/high cloud-confidence pixels are removed;
  uncertainty must be <=3 K and distance from cloud >=1 km. Values are Celsius.
  Provider nodata is removed. Sources retain their dates and scene identifiers.
- Water: USGS / MassDEP hydrography through MassGIS. Select reservoirs/open water
  (`POLY_CODE IN (1,6,8)`), not wetlands. Preserve provider IDs and attribution.
  A fresh download does not imply a new survey: source modification time, when
  supplied, is recorded separately from this pipeline's refresh timestamp.

Source terms remain in the metadata. Landsat data are public domain; credit USGS.
Water retains MassGIS/USGS/MassDEP attribution and terms, without blanket relicensing.

## Seed locally and publish

```powershell
uv run data/context/build.py --city USBOS --output .tmp/usbos-context-seed
# After validation, upload this already-built seed using GOOGLE_HMAC_KEY/SECRET:
uv run data/context/publish.py .tmp/usbos-context-seed --city USBOS
# Or use the workstation's active gcloud login (no credential changes):
uv run data/context/publish.py .tmp/usbos-context-seed --city USBOS --gcloud
# Alternatively build and upload in one operation:
uv run data/context/build.py --city USBOS --publish
```

The `context-usbos` Trilogy job calls `context/refresh_usbos.py` and publishes
monthly, independently of trees/core. It rebuilds both sources, validates them,
then replaces the two stable Parquets and writes metadata last. Validation rejects
empty, duplicate, wrong-city or implausible observations, older heat years,
same-season observation losses over 25%, and water feature losses over 25%.
Uploads use generation preconditions so overlapping runs cannot overwrite newer
assets unnoticed. Each object replacement is atomic; the three files are not a
transaction. Readers requiring a consistent pair should check metadata hashes or
generation numbers and retry during an update. A failed partial publication is
reported as a failure; it does not claim the old metadata describes new bytes.

## Trilogy models

`data/context/usbos.preql` exposes two root, read-only published datasources over
the stable Parquets. `common.preql` defines separate observation/feature grains.
Importing these models never fetches portals or rebuilds trees. Spatial matching
and temporal aggregation remain explicit consumer choices; no implicit join
makes every temperature observation a tree row. New-city adapters follow the same
schema and get their own source-refresh job.

The approved desktop/mobile rendering code remains available. It is independent
of this source publication; the old poster's frozen local analysis is not a
publication input or a requirement for refreshing context.

Primary sources: [USGS surface temperature](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature),
[Planetary Computer](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2),
[MassGIS hydrography](https://www.mass.gov/info-details/massgis-data-massdep-hydrography-125000).
