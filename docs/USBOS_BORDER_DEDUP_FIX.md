# USBOS border dedupe: why the cloud build fails, and the fix

## Summary

Runs 58f27346 (after its bundle fix), 9f2f2648 and a4835779 of `adhoc-usbos-bordertest` did not fail on memory or on the parquet write. DuckDB filled the VM's disk with spill data because the new border-dedupe aggregates make it plan a join that produces billions of rows. Adding memory does nothing. The fix is a change to the model in `data/raw/usbos/boston_tree_info.preql`.

## The error (cut from the CLI's log excerpt)

The full stderr artifact for a4835779 says:

```
Failed to refresh datasource 'boston_tree_info' ... OutOfMemoryException:
Out of Memory Error: failed to offload data block of size 96.0 KiB (12.7 GiB/12.7 GiB used).
This limit was set by the 'max_temp_directory_size' setting.
```

`max_temp_directory_size` defaults to the free disk space where DuckDB spills, so this is the disk filling up. 9f2f2648 failed identically.

## The cause

All the Boston inputs together come to about 161k rows (~25 MB): Boston 52k, Cambridge 33k, Arboretum 16k, Somerville 16k, Brookline 15k, plus OSM, satellite and community. I replayed the exact SQL from the failing run against locally captured ingests, with DuckDB capped at 1 GB memory and 4 GiB spill. Every CTE finished in under a second except `gullible`, which computes `usbos_border_duplicate` and never finished.

`usbos_first_rank(cell)` is `min(usbos_border_rank) by cell, usbos_raw_genus`, and the model calls it once for each of the four staggered grids. That gives four aggregates, each joined back to the row on two conditions:

```sql
INNER JOIN wooden    ON d.usbos_border_cell_a IS NOT DISTINCT FROM wooden.usbos_border_cell_a    AND d.usbos_raw_genus = wooden.usbos_raw_genus
INNER JOIN ceaseless ON d.usbos_border_cell_b IS NOT DISTINCT FROM ceaseless.usbos_border_cell_b AND d.usbos_raw_genus = ceaseless.usbos_raw_genus
-- ...cell_c, cell_d the same
```

Because every condition includes `d.usbos_raw_genus = X.usbos_raw_genus`, DuckDB infers that the aggregates' genus columns all equal one another. Its join-order optimizer then joins the aggregates to each other on genus alone, before any cell condition applies. That is every Acer joined to every Acer, and likewise for each genus. From EXPLAIN on DuckDB 1.5.6:

```
HASH_JOIN usbos_raw_genus = usbos_raw_genus        est. 25,315,489
  HASH_JOIN usbos_raw_genus = usbos_raw_genus      est. 19,892,418,062
    HASH_JOIN [cell_d, genus, cell_c, cell_b]      est. 4.2e18
```

DuckDB 2.0.0.dev2610011535 builds the same join shape with smaller estimates and also never finishes. Upgrading DuckDB is not the fix.

The data is not the cause: no source has a duplicate `tree_id`, and no row has a null genus or null cell.

## The fix

Fold the genus into the cell key so that each aggregate has one grouping key and each join has one condition. With no shared genus column, DuckDB has nothing to chain through.

```
# The genus is part of the cell key, not a second join column: four
# aggregates sharing a genus column let DuckDB join them to each other on
# genus alone (a per-genus cross product, billions of rows on the metro).
def usbos_border_cell(dx, dy) -> concat(
    cast(
        cast(floor(raw_longitude / 0.00002431 + dx) as bigint) * 10000000
        + cast(floor(raw_latitude / 0.00001797 + dy) as bigint)
    as string),
    '|',
    usbos_raw_genus
);
def usbos_first_rank(cell) -> min(usbos_border_rank) by cell;
```

`usbos_border_cell_a`..`d`, `usbos_own_border_rank` and `usbos_border_duplicate` stay as they are.

**What I tested:** I rewrote the four join conditions in the failing SQL as `concat(cell, '|', genus) = concat(cell, '|', genus)`, which is equivalent to the change above. `gullible` then finishes in 0.6s and the whole query in 0.7s, giving 153,229 rows with 96 flagged as border duplicates. This holds on DuckDB 1.5.6 and on 2.0 dev.

**Still to check on the model itself:** render the refresh with `trilogy refresh -n -f <boston_tree_info>` and confirm that each of the four aggregates now groups by one key and joins on one condition.

A behaviour difference: in DuckDB, `concat` turns NULL into `''`, so rows with a null genus would share a key instead of being dropped by the inner join. No current row has a null genus. If one appears, today's SQL drops that tree from the output entirely (`yellow INNER JOIN gullible`), so the concat version is the safer of the two.

## Second problem: the border gate filtered nothing in this run

In the a4835779 SQL, `yellow` renders `False as usbos_border_duplicate` and the final filter becomes `WHERE tree_id = cluster_id and True`. The real computed flag only leaves as an extra column, `usbos_border_flag`, and nothing filters on it. The 96 flagged trees would therefore have been published. This may be specific to the bordertest bundle, which names the flag differently. Before trusting the gate, render the committed model and confirm that a flagged row is actually removed: the published row count should be about 153,229 − 96.

## Reading a run's full log

`runs show` displays only a 64 KiB head and tail of stdout/stderr, and the echoed SQL fills that. The full logs are run artifacts:

```
GET /orgs/{org}/jobs/runs/{run_id}/artifacts/{stderr|stdout|exec_log}  ->  {url, expires_at}
```

`trilogy cloud runs logs <run_id>` is being added to pytrilogy to fetch them.
