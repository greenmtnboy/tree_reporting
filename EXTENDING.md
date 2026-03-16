# Extending the Tree Map: Adding a New City

This document captures the steps discovered when adding Paris (FR), following SF, NYC, and Boston. Use it as a runbook for the next city addition.

---

## Architecture Overview

```
OpenData API / CSV
        │
        ▼
data/raw/{city}/{city}_tree_info.py   ← fetch + transform script (Arrow IPC → stdout)
        │
        ▼  (Trilogy pipeline materialises to GCS)
GCS: trilogy_public_models/duckdb/trees/{code}_tree_info_v{version}.parquet
        │
        ▼  (loaded at runtime by the browser worker)
src/src/workers/duckdbPipeline.worker.ts  ← DuckDB-WASM reads parquet over HTTP
        │
        ▼
src/src/composables/useMapData.ts  ← CITY_CONFIG drives map center + default query
        │
        ▼
src/src/workers/parquetUrls.ts  ← builds versioned GCS URL from city code + DATA_VERSION
```

The browser never touches raw source data — it only fetches the pre-built parquet from GCS and queries it locally with DuckDB-WASM.

---

## Data Versioning

All GCS parquet files use a versioned naming scheme: `{name}_v{DATA_VERSION}.parquet`.

The version is a single integer defined in two places — keep them in sync when bumping:

- **`data/raw/_tree_shared.py`** — `DATA_VERSION = 1` (used by all Python scripts)
- **`src/src/workers/parquetUrls.ts`** — `const DATA_VERSION = 1` (used by the browser worker)

Preql datasource files use the `f\`` template syntax to interpolate the version:

```preql
file f`https://.../trees/{code}_tree_info_v{data_version}.parquet`:f`gcs://.../{code}_tree_info_v{data_version}.parquet`
```

**When to bump the version:** Any schema-breaking change to a parquet (column rename, type change, key refactor). Bump the integer in both places, rebuild all parquets, and the old files remain on GCS untouched as a rollback path.

---

## City Code Convention

| City | Code | Pattern |
|------|------|---------|
| San Francisco | `USSFO` | `{ISO-3166-1-alpha-2}{IATA-airport-or-3-letter-abbr}` |
| New York City | `USNYC` | |
| Boston | `USBOS` | |
| Paris | `FRPAR` | |

All codes are **5 uppercase letters**: 2-letter country code + 3-letter city abbreviation. The parquet file name is the lowercase code: `frpar_tree_info_v1.parquet`.

---

## Step-by-Step: Adding a New City

### 1. Understand the Source Data

Find the city's open tree dataset. Key fields needed:
- **Unique tree ID** (any stable string/int)
- **Species** — the scientific name (Latin binomial, e.g. `"Platanus x hispanica"`). Do **not** embed a common name in this field — see the Species Enrichment section.
- **Latitude / Longitude** (decimal degrees WGS84)
- **Diameter at breast height** (DBH) in inches, or a proxy you can convert

Paris notes:
- Source: `https://opendata.paris.fr/explore/dataset/les-arbres/`
- API: `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres/exports/parquet`
- DBH not available — uses **circumference in cm** (`circonferenceencm`); convert: `dbh_in = circ_cm / (π × 2.54)`
- Genus/species are `genre`/`espece` — concatenate as `f"{genre} {espece}".strip()`
- Location is a nested struct `geo_point_2d: {lat, lon}`
- No `plant_date` field
- ~217k trees

### 2. Choose a City Code

Format: `{ISO2}{3-letter-city}`. Check it doesn't collide with existing codes. For Paris → `FRPAR`.

### 3. Register the City Code in the Trilogy Enum

**`data/raw/core.preql`** holds the `city` key as a typed enum. Add the new code:

```preql
key city enum<string>['USSFO', 'USNYC', 'USBOS', 'FRPAR'];
```

Trilogy will reject any `complete where city = '...'` clause whose value isn't in this enum, so this must be done before the preql files in the next steps will validate.

### 4. Create the Freshness Probe

**This step is mandatory.** Without it, Trilogy re-downloads the full dataset on every pipeline run regardless of whether the source has changed. The probe is a lightweight script that fetches only the dataset's last-modified timestamp and emits a single-row Arrow table.

Create `data/raw/{city}/{city}_update_time.py`:

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

import sys, requests, pyarrow as pa
from datetime import datetime, timezone

def fetch_modified_at() -> datetime:
    # Hit the lightest metadata endpoint your open data platform exposes.
    # For OpenDataSoft (Paris, and many European cities):
    #   GET /api/explore/v2.1/catalog/datasets/{dataset_id}
    #   Read: .metas.default.modified  (ISO 8601)
    # For CKAN (Boston, many US cities):
    #   GET /api/3/action/resource_show?id={resource_id}
    #   Read: .result.last_modified
    # For Socrata (SF, NYC):
    #   GET /api/views/{dataset_id}.json
    #   Read: .rowsUpdatedAt  (Unix timestamp)
    raise NotImplementedError

def emit(updated_at: datetime) -> None:
    table = pa.table({
        "city": pa.array(["{CODE}"], type=pa.string()),
        "data_updated_through": pa.array([updated_at], type=pa.timestamp("us", tz="UTC")),
    })
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)

if __name__ == "__main__":
    emit(fetch_modified_at())
```

Also add the city's freshness property to **`data/raw/tree_common.preql`**:

```preql
property <*>.{city}_data_updated_through datetime;

auto latest_update_through <- greatest(..., {city}_data_updated_through);
```

Paris probe details:
- **Metadata URL:** `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres`
- **Timestamp field:** `.metas.default.modified` (ISO 8601, already timezone-aware)
- This is a ~2 KB JSON response vs. the ~50 MB full dataset export

### 5. Create the Fetch Script

Create `data/raw/{city}/{city}_tree_info.py`. Follow the pattern of `boston_tree_info.py`.

The script must:
1. Download the source data (CSV, JSON, or parquet from the open data portal)
2. Transform to this schema:

| Column | Type | Notes |
|--------|------|-------|
| `tree_id` | `string` | Prefix with `{abbr}-` e.g. `par-12345` for global uniqueness |
| `city` | `string` | The city code, e.g. `FRPAR` |
| `species` | `string` | **Scientific name only** — e.g. `"Platanus x hispanica"`. No `:: Common Name` suffix. |
| `plant_date` | `date32` or `null` | `null` if unavailable |
| `latitude` | `float64` | |
| `longitude` | `float64` | |
| `diameter_at_breast_height` | `float64` | Inches; `null` if unavailable |

3. Emit the Arrow IPC stream to `sys.stdout.buffer` via `pa.ipc.new_stream`.

**Species key rule:** The `species` field must contain only the Latin binomial (e.g. `"Platanus x hispanica"`). The `:: Common Name` convention was retired — common names now come exclusively from the enrichment table. If source data has a `:: suffix` (SF does), strip it: `v.split("::")[0].strip()`. For cities that provide genus and species epithet as separate fields (Paris), concatenate them: `f"{genre} {espece}".strip()`. Empty or null species should be emitted as `None`.

### 6. Create the Trilogy Data Model

Create `data/raw/{city}/{city}_tree_info.preql`. Wire in the freshness probe via `freshness by` on both the raw datasource and the materialized parquet. Use the `f\`` template syntax for the versioned GCS URL:

```preql
import ..tree_common;

root partial datasource {city}_update_time (
    data_updated_through: {city}_data_updated_through
)
grain (city)
complete where city = '{CODE}'
file `./{city}_update_time.py`
freshness by {city}_data_updated_through;

root partial datasource {city}_raw_tree_info (
    tree_id: tree_id,
    city: city,
    species: species,
    plant_date: ?plant_date,
    latitude: ?latitude,
    longitude: ?longitude,
    diameter_at_breast_height: ?diameter_at_breast_height,
)
grain (tree_id)
complete where city = '{CODE}'
file `./{city}_tree_info.py`;


partial datasource {city}_tree_info (
    tree_id,
    city,
    species,
    ?plant_date,
    ?diameter_at_breast_height,
    ?latitude,
    ?longitude,
    {city}_data_updated_through,
)
grain (tree_id)
complete where city = '{CODE}'
file f`https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{code}_tree_info_v{data_version}.parquet`:f`gcs://trilogy_public_models/duckdb/trees/{code}_tree_info_v{data_version}.parquet`
freshness by {city}_data_updated_through;
```

### 7. Import in the Merged Data Model

Add to `data/raw/tree_info.preql`:

```preql
import {city}.{city}_tree_info;
```

### 8. Build and Upload the Parquet via Trilogy

Run the Trilogy pipeline to materialise the per-city parquet and push it to GCS:

```bash
trilogy run data/raw/tree_info.preql --city {CODE}
```

### 9. Update the Frontend

**a) `src/src/workers/parquetUrls.ts`** — the `cityTreeParquetUrl` and `cityLandmarkParquetUrl` functions use a regex `/^[a-z]{2}[a-z]{3}$/` to validate city codes (5 lowercase letters). No code changes needed for new cities — just ensure the `DATA_VERSION` constant matches `_tree_shared.py`.

**b) `src/src/composables/useMapData.ts`** — add the city to `CITY_CONFIG`:

```ts
export const CITY_CONFIG = {
  // ... existing cities
  FRPAR: { name: 'Paris', center: [2.3522, 48.8566] as [number, number] },
} as const
```

The `center` is `[longitude, latitude]` (GeoJSON/MapLibre order). Use the city center, not a corner.

That's it — the city button appears automatically in the UI, the worker loads `{code}_tree_info_v{DATA_VERSION}.parquet` from GCS, and DuckDB queries it exactly like any other city.

---

## Landmarks (Mandatory)

Every city should have a landmarks dataset. Landmarks give the chat agent geographic context and appear in the map UI. The worker silently skips missing landmark parquets, but the experience degrades — treat this as required, not optional.

### Landmark Data Schema

| Column | Type | Notes |
|--------|------|-------|
| `landmark_id` | `string` | Prefix with `{abbr}-` for global uniqueness |
| `city` | `string` | City code |
| `name` | `string` | Human-readable landmark name |
| `geometry_raw` | `string` | WKT geometry. Use a polygon/multipolygon if available; for point-only sources construct `POINT(lon lat)` |
| `latitude` | `float64` | Centroid latitude (can be derived from `geometry_raw` by Trilogy, or set directly for point sources) |
| `longitude` | `float64` | Centroid longitude |

City-specific extra fields are fine — declare them in `landmark_common.preql` alongside the SF, NYC, Boston, and Paris-specific blocks already there.

### Finding a Landmarks Source

Preference order:
1. **Official historic landmark / heritage designation registry** from the city or national government — most consistent with how SF/NYC/Boston landmarks work (e.g. NYC Landmarks Preservation Commission, SF landmark designations)
2. **Same open data platform as the trees** if a heritage dataset exists there
3. OpenStreetMap extract as a last resort

**Paris:** The best source is the national Monuments Historiques registry filtered to Paris (dept 75), hosted on the Île-de-France regional open data platform — not opendata.paris.fr. It has ~1,885 officially classified/listed monuments, parquet export, and point coordinates.

```
Dataset: immeubles-proteges-au-titre-des-monuments-historiques
Platform: data.iledefrance.fr  (OpenDataSoft v2 — same API as opendata.paris.fr)
Parquet export URL:
  https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/
  immeubles-proteges-au-titre-des-monuments-historiques/exports/parquet
  ?where=departement_format_numerique%3D%2275%22
  &select=reference,titre_editorial_de_la_notice,adresse_forme_editoriale,
          commune_forme_editoriale,date_et_typologie_de_la_protection,
          denomination_de_l_edifice,coordonnees_au_format_wgs84
Metadata URL (for probe):
  https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/
  immeubles-proteges-au-titre-des-monuments-historiques
  → .metas.default.modified
```

Key field mapping for Paris:
- `reference` → `landmark_id` (prefix `"frpar-"`)
- `titre_editorial_de_la_notice` → `name`
- `coordonnees_au_format_wgs84` (struct `{lon, lat}`) → construct `POINT(lon lat)` as `geometry_raw`
- `commune_forme_editoriale` → `arrondissement` (Paris-specific)
- `date_et_typologie_de_la_protection` → `protection_type` (Paris-specific, e.g. `"1862 : classé MH"`)
- `denomination_de_l_edifice` → `denomination` (Paris-specific, e.g. `"immeuble"`, `"église"`)

### Files to Create

```
data/raw/{city}/{city}_landmarks.py          ← fetch + transform (same Arrow IPC pattern)
data/raw/{city}/{city}_landmarks_probe.py    ← freshness probe (same pattern as tree probe)
data/raw/{city}/{city}_landmarks.preql       ← datasource with versioned GCS URL
```

Add the city's landmark freshness property and update the `greatest()` expression in **`data/raw/landmark_common.preql`**:

```preql
property <*>.{city}_landmark_data_updated_through datetime;
auto latest_landmark_update_through <- greatest(..., {city}_landmark_data_updated_through);
```

Add the import to **`data/raw/landmark_info.preql`**:

```preql
import {city}.{city}_landmarks;
```

The landmark preql follows the same versioned `f\`` URL pattern as tree files. See `paris_landmarks.preql` as the reference implementation.

---

## Species Enrichment

### How It Works

The `tree_enrichment_v{DATA_VERSION}.parquet` at GCS is city-agnostic. It maps **scientific species names** (Latin binomials only) to:
- `common_names` — comma-separated English common names, most familiar first
- `tree_category` — visual category (`palm`, `broadleaf`, `coniferous`, etc.) used for icon and color
- Ecological metadata: `native_status`, `is_evergreen`, `mature_height_ft`, `bloom_season`, etc.

The browser worker joins on `t.species = se.species` (exact match on scientific name) and derives `common_name` as `split_part(se.common_names, ',', 1)` — the first enrichment common name, falling back to the scientific name if unenriched.

### Species Key Rule (Important)

The `species` field in all tree parquets **must be the scientific name only** (no `:: suffix`). This is what makes the single enrichment table work across all cities:

- SF raw data has `"Platanus x hispanica :: London Plane"` → strip to `"Platanus x hispanica"` in `sf_tree_info.py`
- Paris has separate `genre`/`espece` fields → concatenate to `"Platanus hispanica"`
- NYC/Boston already emit scientific names directly

If you add a city whose source data embeds a common name in the species field (any `::` pattern), strip it in the fetch script before emitting.

### After Adding a New City

Run the enrichment probe to measure coverage:

```bash
cd data/raw && uv run tree_enrichment_probe.py
```

It prints `true` if every species has complete enrichment, `false` + a list of missing species otherwise. Then run `tree_enrichment.py` to fill in missing entries (this calls the LLM — costs money, runs slowly):

```bash
cd data/raw && uv run tree_enrichment.py --limit 50 --output tree_enrichment.parquet
```

### Enrichment Key Migration (Backfill)

If the enrichment parquet was built with old `::` keyed rows (pre-refactor), run the backfill script once to rekey everything to scientific names without re-calling the LLM:

```bash
cd data/raw && uv run backfill_enrichment_keys.py
# or for a dry run:
uv run backfill_enrichment_keys.py --dry-run
```

This reads the existing GCS parquet, strips `::` suffixes, deduplicates (most-complete / most-recent row wins per scientific name), writes locally, and uploads to GCS via `gcloud storage cp`. On Windows, `gsutil` requires Python ≤ 3.11 — use `gcloud storage cp` instead.

---

## What to Optimize / Streamline Next Time

### 2. `core.preql` City Enum Is Easy to Miss
**Problem:** The `city` key in `data/raw/core.preql` is a typed enum. Forgetting to add the new code there causes Trilogy to reject every `complete where city = '...'` clause in the new preql files — but the error message points at the preql files, not `core.preql`.
**Suggestion:** Do this as step 3 (it already is), and double-check it's the very first edit before creating any other files.


### 5. No Automated Enrichment Coverage Check in CI
**Suggestion:** Run `tree_enrichment_probe.py` as a non-blocking CI step after any tree parquet rebuild and post the coverage report as a PR comment. Currently it has to be run manually.
