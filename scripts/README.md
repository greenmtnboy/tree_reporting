# Treelines poster rendering

Run these commands from the repository root. The entry points declare their
Python dependencies inline; `uv run` creates the environment. Normal rendering
reads the published city parquet and public water/boundary services. It never
changes or publishes tree data.

## San Francisco and city presets

```powershell
uv run scripts/render_city_treelines.py --city USSFO
uv run scripts/render_city_treelines.py --city USSFO --palette forest
uv run scripts/render_city_treelines.py --city USSFO --palette night
uv run scripts/render_city_treelines.py --city USSFO --insets hayes_valley mission --dpi 600
uv run scripts/render_city_treelines.py --city USSFO --source-mode all --color-by source --insets alamo_square --coverage-note "Test aerial coverage is partial; detections undercount trees, especially beneath the canopy."
```

The default is a light poster using **only `SF_OPENDATA` municipal street-tree
records**, with Hayes Valley/Civic Center and Mission District insets. No imagery
predictions or OSM records are needed. `--source-mode non-aerial` explicitly adds
other mapped sources; `--source-mode all` includes all surviving sources. These
modes use one tree color by default. `--color-by source` requires `--source-mode
all` and both inventory and aerial records in the viewport. It draws inventory
and other mapped records in ochre on the main map, with aerial detections in
green; the inset separates OSM/other records in gray. With one inset, it adds
source counts and the inventory share of that inset's mapped records. This share
is not inventory completeness against ground truth. `--coverage-note` supplies a
short export-specific caveat, useful for partial prediction runs. The source mode
and selected/excluded source counts are recorded in the JSON output.

`treelines_cities/ussfo.json` holds the input URL, inventory source names, credits,
projection center, viewport, circular fade, water providers, and named insets.
Use `--config` for another rendering preset with the same schema, and a matching
`--city`. Adding a rendering preset does not add a city to the ingestion pipeline.
The city renderer supports unified or source-colored dots, zero to two insets,
water, three palettes, a north marker, and metric scales. Boston's side-by-side
comparison layout, canopy circles, and municipal overlays remain in its
specialized entry point.

The SF main viewport is **20 × 20 km**. Its 9 km-radius artistic circle fades out
at 9.27 km, completely inside that frame. Boston's frozen main viewport is about
23.55 × 24.34 km. Both cities' default inset squares are **2 × 2 km**: the insets
have the same geographic scale, while the main SF view remains more zoomed in.
The circle is a visual crop, not a jurisdiction or prediction-coverage boundary.

Outputs go to `scripts/sf_renders/`, or the path supplied with `--output`. At the
default 18-inch width, 300 dpi produces 5400 × 4644 pixels and 600 dpi produces
10800 × 9288 pixels. `--insets` with no values removes the sidebar insets.
`--no-water` and `--no-circle-fade` are available for diagnostic exports.

The four SF source-comparison sharing insets are:

| `--insets` value | Highlight |
| --- | --- |
| `alamo_square` | Alamo Square & Buena Vista Park |
| `golden_gate_inner_sunset` | Golden Gate Park Lakes (centered on Blue Heron Lake) |
| `presidio_marina` | Palace of Fine Arts & Marina |
| `bernal_mission` | Bernal Heights & the Mission |

Render each with `--source-mode all --color-by source`. Use the same local
parquet snapshot via `--input` for all four when producing a consistent set.
Name outputs `san_francisco_treelines_<inset>_city_and_aerial.png`, then run
`uv run scripts/render_sf_review_grid.py` to make a four-inset comparison grid.
The grid accepts `--input-dir` and `--output` and supports 300 or 600 dpi posters.

For the three-color SF land-setting poster, run
`uv run scripts/render_sf_tree_settings.py` (optionally `--input SNAPSHOT.parquet`).
It uses public CPAD park units, SF Public Works rights-of-way, and active parcels
matched to the 2025 assessor roll. Park locations take precedence at overlaps.
Gray dots show trees on other or unresolved land. The likely private subtotal
separates residential, business/industrial, golf-course and mixed-use footprints.
The accompanying JSON includes all park counts, source splits, and layer provenance.
Southern out-of-city trees appear in gray for context, excluded from every
displayed total and category. Unclassified trees within SF remain in the counts.

## Boston presets

```powershell
# Four source-colored sharing posters: Mount Auburn, Arboretum, Harvard Square,
# and Back Bay/Public Garden. Uses the frozen north-shifted geographic frame.
pwsh -File scripts/render_boston_sharing_set.ps1

# Unified trees, ochre municipal boundaries, and stacked Back Bay + Arboretum
# insets, at both 300 and 600 dpi.
pwsh -File scripts/render_boston_followups.ps1

# Custom single-inset variant.
uv run scripts/render_boston_sources_map.py --palette light --inset harvard_square --inset-canopy --compact --cartographic-details --north-shift-km 1.5 --frame-from scripts/boston_sharing_frame.json
```

The Boston renderer also supports `--layout comparison`, `--unified`,
`--municipal-boundaries`, and `--second-inset`. Run `--help` for compatible
combinations. `--frame-from` reuses the projection and final bounds; it does not
apply the viewport shift twice. The north shift still positions the circular
fade. The follow-up runner checks publication stamps and counts across exports
and fails if those change during the run.

`boston_municipal_boundaries.py` uses MassGIS municipal polygons, retaining only
edges shared by municipalities. Coastlines and island perimeters are omitted;
actual jurisdiction lines across rivers remain. `inspect_boston_imagery_seams.py`
is a separate read-only diagnostic for two historical blank NAIP strips. It
requires the original locally cached rasters and manifests and does not repair
or rerun predictions.

## Shared code and data interpretation

- `treelines_core.py`: palettes, dots/glow, projected canopy circles, circular
  fade, mixed-style text, and north/scale ornaments used by both renderers.
- `treelines_data.py`: city-independent published-record selection, projection,
  and cached Socrata/ArcGIS water loading for the preset-based renderer.
- Each PNG has a JSON sidecar with inputs, selected counts, inset bounds, source
  provenance, cache hashes, projection, and rendering options. Remote inputs can
  change; use `--input` with a local parquet snapshot for repeatable tree data.
  Boston also accepts `--crowns-input` for a local canopy-estimate snapshot.

Counts refer to published surviving records in the rectangular viewport, before
artistic fading. Visibility flags and cluster-survivor rules are respected.
These are not censuses of every existing tree. SF's street-tree inventory does
not comprehensively map parks or private land. Boston imagery detections can
miss under-canopy trees and contain errors; inventories may also be incomplete
or outdated. An inventory share describes mapped records in its inset, not
inventory completeness against verified ground truth. Canopy circles use source
widths or joined estimates, with missing widths shown as dots only.

SF water combines DataSF's local water bodies with California Department of
Technology's NOAA CUSP-based coastal polygons. Boston water comes from the
MassGIS water service. Water/boundary responses are cached in `.tmp/`; removing
the relevant cached GeoJSON refreshes them. Generated exports, archives, and
machine-local metadata are ignored by Git; presets such as
`boston_sharing_frame.json` are versioned.

## Offline checks

```powershell
uv run --no-project --with duckdb --with matplotlib --with numpy --with requests --with scipy --with "shapely>=2.1" python -m unittest discover -s scripts/tests
```

These checks cover inventory-only selection, source opt-ins and color grouping, visibility and
cluster filtering, empty selections, coastline removal, and the SF circle's fit
within the viewport, park/street precedence, public-land exclusions, and the
private golf-course split. These offline checks also run in CI.
Rendering smoke checks additionally require network access
or local input/cache files.
