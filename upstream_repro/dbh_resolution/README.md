# Persist projection loses a `merge`-populated property when a root-probe watermark column is present

Minimized from `data/raw/usbos/boston_tree_info.preql`, whose dbh-imputation
(`merge processed_dbh into diameter_at_breast_height`) has blocked Boston's
parquet refresh since Aug 7. The failure is **not** about the imputation
aggregate at all — a trivial scalar derivation triggers it identically.

## The bug

Three ingredients, all required (ablating any one of them resolves the plan
correctly):

1. A declared property populated **only** via `merge`:
   `property entity_id.measure float;` +
   `auto derived_measure <- raw_measure + 1.0;` +
   `merge derived_measure into measure;` — no datasource maps `measure`
   directly.
2. A `<*>`-scoped datetime property sourced from a **separate** root
   datasource (the standard freshness-watermark pattern).
3. A managed (non-root) datasource declaring **both** concepts as columns.

When the managed datasource is refreshed, the planner joins the watermark CTE
to the row CTE (`FULL JOIN ... on 1=1`) and the merge-populated property falls
out of the final projection.

## Behavior by pytrilogy version (all tested with this repro)

| Version | Result |
|---|---|
| 0.3.315 | Correct SQL — `measure` present in the persist projection |
| 0.3.316 | **Regression starts**: plan "succeeds" but `measure` is silently missing from the INSERT projection (2 of 3 declared columns written) |
| 0.3.320, 0.3.325, 0.3.328, 0.3.330 | Same silent column drop, reported as OK |
| 0.3.331 | New guard (`_validate_persist_projection`, `trilogy/core/query_processor.py`) turns the drop into a hard planning error |
| 0.3.332 (latest as of 2026-08-18) | Same hard error |

Exact exception on 0.3.331+:

```
trilogy.core.exceptions.UnresolvableQueryException: Persist to merged_rows_out
would write 2 of 3 declared columns: the plan's final projection has no source
for measure (local.measure). Writing it would shift every later column into
the wrong field.
```

(Surfaced by the refresh path as `RefreshAssetError: Failed to refresh
datasource 'merged_rows' (stale because: forced rebuild): ...` — the CLI
prints the same text.)

So 0.3.331/0.3.332 did not introduce the failure; they introduced the
validation that catches a silent-wrong-data bug that landed in 0.3.316.
0.3.316 is also the release that introduced the known bridge-prune regression
(merged-unnest bridges joined `on 1=1` being dropped — see
`../cte_cycle/README.md` history); the watermark join here is exactly an
`on 1=1` join, so this is plausibly the same pruning change.

Against the real Boston model on 0.3.330, `trilogy refresh
raw/usbos/boston_tree_info.preql --dry-run -f boston_tree_info` reports
success while the generated INSERT selects 10 of the 11 declared columns —
`diameter_at_breast_height` is simply absent.

## Expected vs actual

**Expected:** the persist projection contains `measure`, computed from its
merged derivation (as 0.3.315 does):

```sql
SELECT "quizzical"."entity_id", "quizzical"."measure", "highfalutin"."updated_through"
FROM "quizzical" FULL JOIN "highfalutin" on 1=1
```

**Actual (0.3.316-0.3.330):** `measure` vanishes from the projection; the
refresh still reports success.
**Actual (0.3.331+):** `UnresolvableQueryException` as above; the datasource
can never refresh.

## Ablations (measured on 0.3.332)

| Change | Result |
|---|---|
| As-is (`repro.preql`) | FAILS |
| Derivation is a partitioned imputation aggregate `coalesce(x, avg(x) by k1, k2)` (the Boston shape) | FAILS identically |
| Drop the `merge`; declare `auto measure <- ...` directly | OK |
| Drop the watermark column from the managed datasource | OK |
| Keep the watermark column, drop only `freshness by` | still FAILS (the clause is irrelevant; the column is the trigger) |
| Multiple partial partitions with `complete where` (Boston shape) | FAILS identically (not required) |

## How to run

Fully offline — root datasources are inline `query` blocks, and the refresh is
dry-run.

```bash
cd upstream_repro/dbh_resolution

# Driver (pins pytrilogy==0.3.332 via inline script deps):
uv run repro_refresh.py

# Same failure through the CLI:
uv run trilogy refresh repro.preql duck_db --dry-run -f merged_rows

# Any other version:
uv run --no-project --with pytrilogy==0.3.315 python repro_refresh.py   # OK, correct SQL
uv run --no-project --with pytrilogy==0.3.316 python repro_refresh.py   # "OK", measure silently dropped
uv run --no-project --with pytrilogy==0.3.331 python repro_refresh.py   # UnresolvableQueryException
```

The driver prints the generated persist SQL (`--- SQL for merged_rows ---`);
on the silent-drop versions, grep it for `as "measure"` to see the column
missing.

## Files

- `repro.preql` — the minimized model (3 concepts + derivation + merge, 2 root
  datasources, 1 managed datasource).
- `repro_refresh.py` — offline driver using `create_refresh_plan` /
  `execute_refresh_plan(dry_run=True)`, the same planner path as
  `trilogy refresh --dry-run -f`.
