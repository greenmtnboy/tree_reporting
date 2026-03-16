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
GCS: trilogy_public_models/duckdb/trees/{code}_tree_info.parquet
        │
        ▼  (loaded at runtime by the browser worker)
src/src/workers/duckdbPipeline.worker.ts  ← DuckDB-WASM reads parquet over HTTP
        │
        ▼
src/src/composables/useMapData.ts  ← CITY_CONFIG drives map center + default query
        │
        ▼
src/src/workers/parquetUrls.ts  ← builds GCS URL from city code
```

The browser never touches raw source data — it only fetches the pre-built parquet from GCS and queries it locally with DuckDB-WASM.

---

## City Code Convention

| City | Code | Pattern |
|------|------|---------|
| San Francisco | `USSFO` | `{ISO-3166-1-alpha-2}{IATA-airport-or-3-letter-abbr}` |
| New York City | `USNYC` | |
| Boston | `USBOS` | |
| Paris | `FRPAR` | |

All codes are **5 uppercase letters**: 2-letter country code + 3-letter city abbreviation. The parquet file name is the lowercase code: `frpar_tree_info.parquet`.

---

## Step-by-Step: Adding a New City

### 1. Understand the Source Data

Find the city's open tree dataset. Key fields needed:
- **Unique tree ID** (any stable string/int)
- **Species** — ideally formatted or formatable as `"Scientific Name :: Common Name"`
- **Latitude / Longitude** (decimal degrees WGS84)
- **Diameter at breast height** (DBH) in inches, or a proxy you can convert

Paris notes:
- Source: `https://opendata.paris.fr/explore/dataset/les-arbres/`
- API: `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/les-arbres/exports/parquet`
- DBH not available — uses **circumference in cm** (`circonferenceencm`); convert: `dbh_in = circ_cm / (π × 2.54)`
- Common name is French (`libellefrancais`); genus/species are `genre`/`espece`
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

Create `data/raw/{city}/{city}_tree_info.py`. Follow the pattern of `boston_tree_info.py`:

```python
#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///
```

The script must:
1. Download the source data (CSV, JSON, or parquet from the open data portal)
2. Transform to this schema:

| Column | Type | Notes |
|--------|------|-------|
| `tree_id` | `string` | Prefix with `{abbr}-` e.g. `par-12345` for global uniqueness |
| `city` | `string` | The city code, e.g. `FRPAR` |
| `species` | `string` | `"Genus species :: Common Name"` format |
| `plant_date` | `date32` or `null` | `null` if unavailable |
| `latitude` | `float64` | |
| `longitude` | `float64` | |
| `diameter_at_breast_height` | `float64` | Inches; `null` if unavailable |

3. Emit the Arrow IPC stream to `sys.stdout.buffer` via `pa.ipc.new_stream`.

### 6. Create the Trilogy Data Model

Create `data/raw/{city}/{city}_tree_info.preql`. Wire in the freshness probe via `freshness by` on both the raw datasource and the materialized parquet:

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
file `https://storage.googleapis.com/trilogy_public_models/duckdb/trees/{code}_tree_info.parquet`:`gcs://trilogy_public_models/duckdb/trees/{code}_tree_info.parquet`
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

**a) `src/src/workers/parquetUrls.ts`** — the `cityTreeParquetUrl` and `cityLandmarkParquetUrl` functions use a regex to validate city codes. The original regex `/^us[a-z]{3}$/` only matched US cities; it was updated to `/^[a-z]{2}[a-z]{3}$/` to support any 2-letter country code. No further changes needed for new cities.

**b) `src/src/composables/useMapData.ts`** — add the city to `CITY_CONFIG`:

```ts
export const CITY_CONFIG = {
  // ... existing cities
  FRPAR: { name: 'Paris', center: [2.3522, 48.8566] as [number, number] },
} as const
```

The `center` is `[longitude, latitude]` (GeoJSON/MapLibre order). Use the city center, not a corner.

That's it — the city button appears automatically in the UI, the worker loads `{code}_tree_info.parquet` from GCS, and DuckDB queries it exactly like any other city.

---

## Landmarks (Optional)

If you have a landmarks dataset (museums, parks, notable locations), create:
- `data/raw/{city}/{city}_landmarks.py` — same Arrow IPC pattern
- `data/raw/{city}/{city}_landmarks.preql`
- Upload `{code}_landmark_info.parquet` to `gs://trilogy_public_models/duckdb/landmarks/`

If no landmarks file exists, the worker's landmark load wraps in `try/catch` and silently skips it.

---

## Species Enrichment

The `tree_enrichment.parquet` at GCS is city-agnostic. It maps species names to visual categories (palm, broadleaf, coniferous, etc.), icons, and ecological metadata. If a new city introduces species not yet in the enrichment table, run `data/raw/tree_enrichment.py` to update it.

For Paris specifically, species are stored as `"Genre espece"` (Latin binomial) rather than SF's `"Genus species :: CommonName"` format. The enrichment lookup uses case-insensitive `species` matching, so results will vary until enrichment data is added for European species.

---

## What to Optimize / Streamline Next Time

The following friction points were identified during the Paris addition:

### 1. `parquetUrls.ts` Regex Hardcoded to US
**Problem:** The regex `/^us[a-z]{3}$/` silently returned `null` for non-US cities, causing the worker to fall back to the full multi-city dataset instead of the faster per-city file.
**Fix applied:** Updated to `/^[a-z]{2}[a-z]{3}$/`.
**Better long-term:** Replace the regex with an explicit allowlist derived from `CITY_CONFIG` so unknown codes fail loudly rather than silently degrading.

### 2. `core.preql` City Enum Is Easy to Miss
**Problem:** The `city` key in `data/raw/core.preql` is a typed enum. Forgetting to add the new code there causes Trilogy to reject every `complete where city = '...'` clause in the new preql files — but the error message points at the preql files, not `core.preql`, making it non-obvious.
**Fix applied:** Added `FRPAR` to the enum during the Paris addition.
**Suggestion:** Add a comment in `core.preql` and in this document (step 3 above) to make this the first thing you do when adding a city.

### 3. Species Enrichment Coverage for Non-English Cities
**Problem:** Paris trees use French common names and Latin binomials. The enrichment table was built from English-language datasets and won't match most Paris species.
**Suggestion:** After ingesting a new city's data, run an enrichment probe (`data/raw/tree_enrichment_probe.py`) to measure coverage and add missing species in bulk.

### 4. No Landmarks for Paris
**Problem:** There's no `frpar_landmark_info.parquet`, so the chat agent won't know Paris landmarks. The UI silently skips missing landmark files (good), but the agent loses context.
**Suggestion:** Source Paris landmarks from OpenStreetMap or a similar dataset and create `data/raw/paris/paris_landmarks.py`.

