# AGENTS.md

Urban Tree Reporting: an interactive map of municipal, community-submitted,
OpenStreetMap and reviewed aerial-imagery trees for forty-odd cities, with
species enrichment, analytics dashboards and an agent chat over the same data.
`README.md` is the public description; this file is the working guide.

## Layout

| Path | What it is |
|---|---|
| `src/` | The web app: Vite, Vue 3, TypeScript. DuckDB-WASM reads published parquets in the browser; the dashboards and chat compile PreQL through a hosted Trilogy resolver. |
| `data/` | The Trilogy models and ingest scripts that build those parquets, and `trilogy.toml`, the job table trilogy-cloud runs them from. `website/` publishes the arborary.world homepage feed and `world/` the map's geo blocks. `data/raw/` holds the core model, one directory per city, the shared ingest library and the tests; `osm_staging/`, `overture_staging/` and `landmark_staging/` the per-city staging jobs. |
| `reviewer/` | The local reviewer for community submissions and aerial-imagery detections; the only path that publishes a submission. |
| `imagery_model/` | The tree-detection model over NAIP imagery and its export into the reviewer. |
| `terraform/` | Infrastructure. |
| `docs/` | Reference docs: `DATA_PIPELINE.md`, `SPECIES_ENRICHMENT.md`, `LANDMARKS.md`, `TESTING.md`, plus source surveys and calibration notes. |

## Tech stack and tooling

- Frontend: Vite, Vue 3, TypeScript, Pinia, MapLibre, DuckDB-WASM, Vitest, Playwright.
- **Use pnpm for everything under `src/`. Never npm.**
- Data: Python 3.13 scripts run with `uv`, PyArrow, DuckDB, pytrilogy (the
  Trilogy planner) and trilogy-cloud for scheduling. Parquets live in a public
  GCS bucket; the browser never touches a portal.
- Species enrichment is an LLM job (`data/raw/enrichment/`) writing one
  species-keyed parquet every city joins to.

## Commands

```bash
# frontend, from src/
pnpm test            # vitest, offline apart from two resolver-backed suites
pnpm lint
pnpm test:e2e        # Playwright against a --mode e2e build
pnpm test:queries    # compiles and executes the whole dashboard catalog against the live resolver; gates PRs
pnpm test:wasm       # the tile worker on DuckDB-WASM under Node, ~20 s, offline; run after any worker SQL change
pnpm bench:chat      # the agent chat benchmark; spends the demo model budget, run by hand
pnpm bench:memory    # renderer memory and bytes for one city load against a preview; run before and after any loader change (docs/MOBILE_LOAD_BUDGET.md)
pnpm bench:nearby    # every request between the Near Me tap and the first trees on a phone, under a shaped network (MAP_BENCH_NET=4g); run before and after any change to the worker's start or the phone's cell loads
pnpm bench:tiles     # time to settle after each zoom and pan, and how long tiles trickle in; run before and after any tile-query change
pnpm bench:nearby    # a phone's wait from the Near Me tap to trees on a shaped link, with the request waterfall; run before and after any change to the worker's start
pnpm bench:dashboard # the summary dashboard's first open on a phone: bytes per file, renderer memory, time until its queries stop

# data, from data/raw
uv run --no-project --with pytest --with pyarrow --with "pytrilogy>=0.3.540" --with "duckdb<2" --with requests python -m pytest tests -q      # offline once DuckDB's spatial extension is cached; run after touching models or the job table
cd data && trilogy refresh --dry-run raw/{code}/{slug}_tree_info.preql   # must report exactly one asset
```

## Invariants to keep in mind

The reference for each is `docs/DATA_PIPELINE.md`.

- **Each city is an independent pipeline** (`osm-{code}`, `city-{code}`, `landmarks-{code}` jobs); the daily core reads only published parquets and never a portal. A city model's imports decide its job's bundle, so the dry run above must show one asset.
- **A missing job is silent**: nothing errors when a city has no schedule. `data/raw/tests/test_cloud_jobs.py` is what catches it.
- **The frontend's model bundle and the enrichment job's file set are different on purpose.** Putting a second tree source in the browser's scope changes join types and chart answers.
- **Every tree row's `species` is a scientific name**, normalised centrally by `enforce_tree_schema`; common names come only from the enrichment table. Sentinels (`Unknown`, `Palm`, ...) are hardcoded, never enriched.
- **One municipality is one city, and a town among neighbours is a polygon.** A city model unions the sources inside one municipality (Boston: the city, the Arboretum, Northeastern), never a metro's towns. A town that interlocks with its neighbours has its real boundary in `data/raw/boundaries/{code}.json`: that file makes its territory the polygon, carves it out of the rectangle city around it, clips both inventories to the line, and keeps the town's OSM extract inside it. No model carries a polygon, and no worker reads the files: `tools/publish_boundaries.py --publish` uploads them as one content-hashed parquet that `shared/boundary.py` names, and the cloud bundle excludes them. A row with no municipal attribution (community, satellite, predicted) belongs to the town it stands in, whatever city it names. Splitting a metro is in `EXTENDING.md`.
- **Do not hand-write a city addition.** `data/raw/tools/new_city.py` writes the registry edits and `test_city_wiring.py` names what is still missing; the judgement steps (field mapping, freshness probe, landmarks, dedup cell size) are in `EXTENDING.md`.
- **Do not work around a planner bug in a query.** File a repro under `upstream_repro/` (gitignored) and let the test stay red; see `docs/TESTING.md`.
- **The map draws the world, and a city is a bookmark.** The worker never loads a city: it reads the manifest of geo blocks `publish-world-blocks` writes (one parquet per Web Mercator z12 tile, every inventory's trees in it) and holds the blocks near the camera, so a pan or a flight into another city's trees needs no switch and the bookmark (URL, selector, landmarks, chat scope) follows the camera. Blocks and the vocabulary are content-addressed and the manifest is the commit point; a read on the hot path names the cells it is about, never the `trees_fast` view over every cell held. See `docs/SEAMLESS_WORLD.md`.
- **The map has two renderers.** The desktop draws its trees from typed arrays in one WebGL layer (`composables/treeDotsLayer.ts`), read in chunks of one global grid (Web Mercator z12, `workers/cityPoints.ts`; a chunk is one block file): no tree tiles, and a colour change re-reads one byte per tree. A phone, which holds a sixteenth of a block at a time, keeps the vector tiles. A change to how trees are coloured or filtered must reach both, and must be a rule over the row or a table keyed by tree id: the trees held change as the camera moves. See `docs/MAP_COLOR_PERF_AUDIT.md` (third and fourth rounds).
- **The resolver is sent consumption models only.** `src/src/trilogyModels.ts` lists what every dashboard and chat request carries, and the resolver parses all of it each time: no city build model, no merge, no watermark. `data/raw/tree_info.preql` is written from the city models by `tools/sync_app_models.py` (one root partial datasource per published parquet with its `complete where`, plus the rollup); rerun it after a change to what a city publishes. `test_app_models.py` and `test_browser_bundle.py` hold both.
- **The chat's `imports` decide what the browser downloads.** Both screens must reach a tree datasource; `dashboard-pushdown.test.ts` pins it.
- **What the chat publishes to the map is a per-tree rule, asked of every cell as it loads.** `publish_results` compiles a rule against `src/src/models/map_trees.preql` (the worker's `trees` view, never beside `tree_info`), so a filter or colouring follows the camera into the next city. A query that ranks, limits or aggregates is one bounded answer instead, and it and the map's `run_query` are refused without a city filter or a latitude and longitude range (`lib/chatQueryScope.ts`). See `docs/SEAMLESS_WORLD.md` (What the chat publishes).
- **City datasources extend shared templates, and every published parquet is guarded.** Partitions extend `tree_partition`, targets `published_tree_info` (`tree_dedup.preql`); landmarks, OSM staging and crown slices have theirs. Each target carries `assert` ratio guards (`kept`, `source_kept` in `core.preql`) that refuse a build losing a quarter of its rows; use a ratio, never a hardcoded row floor. Tests read a model's composed datasources by parsing it (`parsed()` in `test_data_sources.py`), not by grepping the text.
- **Every city has exactly one position policy.** A city imports `tree_position` (Overture lookup, corrected `latitude`/`longitude`, `source_*` kept) or merges `merged_latitude`/`merged_longitude` itself; `tree_dedup` merges neither. The grid key is derived in `tree_position.preql` from the row's own coordinates (pytrilogy 0.3.360+), not stamped on the rows; see `docs/POSITION_CORRECTION.md`.

## Where to read next

- `EXTENDING.md`: the city-addition runbook.
- `docs/REJECTED_SOURCES.md`: leads turned down (licence, unofficial copies, territory) and why; add one when you drop a lead.
- `docs/DATA_PIPELINE.md`: jobs, partitions, sources, dedup, rebuilding, predictions.
- `docs/SPECIES_ENRICHMENT.md`: the species key rule, synonyms and misspellings, sentinels, the enrichment job.
- `docs/LANDMARKS.md`: landmark sources, schema, staging and building.
- `docs/POSITION_CORRECTION.md`: moving trees out of buildings and roads with Overture; the pilot's numbers; what the language would need to own it.
- `docs/TREE_HEIGHT.md`: `height_m` from LIDAR canopy surveys (Boston metro pilot); crowns are a grid lookup every tree row asks, never a source; match rates per source.
- `docs/BORDER_DEDUP.md`: trees two neighbouring inventories both record (Boston's metro); the parked model block, the planner bugs it waits on, what the language would need.
- `docs/TESTING.md`: the dashboard query sweep, what the chat resolves against, the chat benchmark.
- `docs/SEAMLESS_WORLD.md`: the geo blocks and their manifest, how the worker holds cells of them, scoped reads, the bookmark that follows the camera, what is still a city, what is not done.
- `docs/MOBILE_LOAD_BUDGET.md`: why big cities crash phone tabs, the measured peaks, the row shape the worker keeps, the cell plan (now the world's blocks); `docs/TRILOGY_BUCKETED_OUTPUT_SPEC.md` is the upstream ask that would make the blocks a declaration.
- `data/raw/tools/README.md`: the workstation-only scripts.
- `reviewer/README.md`, `imagery_model/README.md`: the two other applications.
