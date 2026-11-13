# Reproducible environmental context

The Boston “Summertime Shadeness” study joins estimated tree crown cover to
Landsat surface temperature. Its approved desktop and separate mobile targets
are `scripts/render_boston_canopy_grid.py` and
`scripts/render_boston_canopy_mobile.py`. They share the measurement pipeline;
the mobile composition does not replace the desktop poster. The older income
and historical-trend experiments remain on their original research branch.

## Bundle and publication

Binary data belongs in immutable object storage, not Git. Code, source adapters,
tests and documentation live here. The exporter reads existing frozen inputs
and refuses to refresh them from portals:

```powershell
uv run scripts/export_boston_context.py --source-root CHECKOUT --output NEW_BUNDLE
uv run scripts/context_release.py verify NEW_BUNDLE
uv run data/website/publish_context.py NEW_BUNDLE
# Explicit upload; requires GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET.
uv run data/website/publish_context.py NEW_BUNDLE --publish
```

The publisher defaults to validation only. A printed **NOT uploaded** URL is a
proposed address, not a live download. Uploads are create-only, with correct MIME
types and immutable caching. The manifest is uploaded last. Failed runs can be
retried; no mutable “latest” pointer or old-release deletion is involved.

Manifest URL pattern:

```text
https://storage.googleapis.com/trilogy_public_models/arborary/context/v1/{study}/releases/{manifest-sha256}/manifest.json
```

After a successful upload, use that exact URL in the gallery's data/source links.
The manifest itself lists relative asset names, SHA-256 hashes, lengths, roles,
CRS, units, dates, source provenance and replay destinations. Keep original source
rights and attribution; a bundle is not a new blanket licence. Tree data includes
© OpenStreetMap contributors (ODbL), municipal and Arnold Arboretum inventories,
community records, and predictions from USDA NAIP imagery.

| Asset | Purpose |
| --- | --- |
| `analysis_cells.parquet` | GeoParquet 1.1, EPSG:32619, 500 m squares, all 1,430 cells including missing/ineligible observations; `used_in_metro_plot` identifies the 361 plotted cells. |
| `water.parquet` | GeoParquet 1.1, EPSG:4326; 681 original MassGIS features with provider IDs and attributes. Reservoirs/open water only (`POLY_CODE IN (1,6,8)`), not wetlands. |
| `surface_temperature.tif` | Five-band Cloud Optimized GeoTIFF; float32 Celsius, NaN nodata, EPSG:32619, 30 m delivered grid, acquisition dates in band descriptions. Base values roundtrip exactly. |
| `replay/…` | Frozen tree/crown Parquets, original water/heat caches, base analysis and cached crown unions. Includes all eleven available dates in the legacy heat cache; the interoperable raster contains the five selected dates only. |
| `replay/scripts/boston_canopy_renders/boston_canopy_heat_twelve_regions.json` | Full approved analysis/selection report, including view bounds and scientific definitions. Not restored over generated outputs. |

The interoperable layers total approximately 10 MB; the complete replay bundle
is approximately 196 MB, mostly tree inputs. Parquet retains optional fields
even when the first row has no usable heat. Null metrics are not zeros.

## Download and replay

Check out the release's code commit (or matching code hashes), then:

```powershell
uv run scripts/context_release.py fetch IMMUTABLE_MANIFEST_URL NEW_BUNDLE
uv run scripts/context_release.py restore NEW_BUNDLE --checkout CHECKOUT
cd CHECKOUT
uv run scripts/render_boston_canopy_grid.py
uv run scripts/render_boston_canopy_mobile.py
# Retained alternative mobile composition:
uv run scripts/render_boston_canopy_mobile.py --layout stacked
```

Download verifies the manifest's hash against its URL and checks every asset.
Restore verifies renderer/config hashes (LF-normalized), validates all inputs
first, rejects paths outside approved cache/output directories, and refuses to
overwrite different local inputs. Use a fresh checkout for a different study
revision. All required data is local after restoration. Delete the restored
canopy-grid cache only when intentionally recalculating crown unions; that is
slower. Fonts/platform/library changes can affect raster output; the frozen
inputs and methods, not cross-platform byte-identical PNGs, are the contract.

## Measurement contract

- Dates: 2025-06-04, 07-22, 07-30, 08-23 and 08-31. These are five clear-sky
  observations, not consecutive days or an all-weather summer climatology.
- USGS Landsat 8/9 Collection 2 Level-2 surface temperature, accessed through
  Microsoft Planetary Computer. Thermal sampling is about 100 m; delivered
  pixels are 30 m. Those delivered pixels are not independent 30 m measurements.
- QA excludes fill, cloud/cirrus/shadow/snow/water, medium/high cloud confidence,
  uncertainty above 3 K and pixels within 1 km of cloud. Overlapping same-date
  scenes are averaged where valid. Apply the water geometries and retain only
  pixels valid on **all five dates**, then average the same land pixels per date.
  Use the TIFF's base resolution for statistics, not its display overviews.
- Crowns use source widths first, then published DBH-based estimates for missing
  widths. Missing widths stay missing. Union circles, count overlaps once and
  clip to land, using 2 m pixel-center sampling with a 1 m sensitivity check.
- The paired sample requires at least 90% common land-heat coverage, at least
  30 delivered valid pixels and crowns for at least 95% of mapped land-tree
  centers. Exclude squares with **more than** 50% water; exactly 50% is retained.
- Twelve non-overlapping 2 km views contain named landmarks and sixteen 500 m
  cells each. Selection requires heat coverage; it does not optimize the
  displayed temperature/canopy correlation. The map is correlational, not causal.
- Trees are a November 2026 snapshot; heat is summer 2025. This is not a temporal
  planting-effect study or a claim of complete ground-truth canopy coverage.

## Extending to other places and sources

Keep source assets separate from study aggregates. An additional adapter should
emit a versioned manifest with provider/collection IDs, acquisition time or
date ranges, CRS, extent, units, nodata, native versus delivered resolution,
quality masks, processing method, checksums and source rights. The Boston exporter
is one adapter; `context_release.py` and the publisher are source-independent.

Mosaic only compatible heat products on matching dates before temporal averaging;
deduplicate overlapping scene contributions and record any reprojection. Do not
blend air and surface temperature or incompatible sampling times into one value.
For water, preserve original feature IDs and provider boundaries, declare source
priority in overlaps, and union geometries only for the analysis land mask. A
different water vintage, quality rule or source blend requires a new release.

Primary sources: [USGS surface temperature](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature),
[Planetary Computer collection](https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2),
[MassGIS hydrography](https://www.mass.gov/info-details/massgis-data-massdep-hydrography-125000).
