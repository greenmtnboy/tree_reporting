# Workstation tools

Scripts a person runs by hand. Nothing here is named in a `file` clause or
imported by a runtime script, so the whole directory is excluded from the
workspace bundle `trilogy cloud sync` ships (`[cloud] exclude` in
`data/trilogy.toml`, kept honest by
`tests/test_cloud_jobs.py::test_excluded_files_are_not_reachable`).

Run them from `data/raw`, which is what each one's paths and `uv run`
invocation assume:

| tool | what it does |
|------|--------------|
| `new_city.py` | writes the mechanical registry edits a new city needs, from one spec. `--dry-run` first. See `EXTENDING.md`. |
| `osm_dedup_validation.py` | measures a city's mutual-nearest-neighbour rate per distance band, which is what its `DEDUP_CELL_METRES` row is calibrated from. Never copy a cell size from another city. |
| `dedup_cells.py` | renders `DEDUP_CELL_METRES` into `tree_dedup.preql` as an inline `VALUES` table. `--check` is what `test_dedup_cells.py` asserts. |
| `species_audit.py` | finds published names within two edits of each other and asks POWO to adjudicate each pair. `--map` emits entries for `SPECIES_SYNONYMS` / `SPECIES_MISSPELLINGS`. Never curate those by eye. |
| `portal_cadence.py` | records every freshness probe's watermark in `portal_cadence.json` and derives each portal's real publishing interval, compared against the cron in `trilogy.toml`. |
| `crown_allometry_fit.py` | refits the genus-level crown-width model on Tallo and rewrites `crown_width_coefficients.csv` plus the `crown_fallbacks` block in `tree_predictions.preql`. |
| `dbh_age_fit.py` | the same for the age-to-diameter fallback, fitted on the rollup's own dated, measured trees. |

The enrichment table's two hand tools are not here -- they live beside the
package they edit, as `enrichment/admin/server.py` and
`enrichment/backfill.py` -- and they are excluded from the bundle for the
same reason.
