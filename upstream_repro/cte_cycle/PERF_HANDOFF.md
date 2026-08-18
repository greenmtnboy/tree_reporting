# Handoff: exponential planning cost in enum-partitioned union selection

> **STATUS 2026-08-17: FIXED** on `more-cloud-work`. `_best_enum_union` now runs a
> dynamic program over enum values keyed by partial overlap signature instead of the
> `k^V` combo product, with exact tie-break parity to the old enumeration (candidate
> index tuples decide equal-score ties and result ordering). Measured: n=12
> 31.0s → 0.6s plan+render, n=14 1.0s, growth flat. The old product implementation is
> kept as a randomized-equivalence oracle in
> `tests/generators/test_datasource_scoring.py`, alongside a 12-value scale guard.
> Verified: TPC-DS 172/172, full sweep 8141 passed (2 unrelated network flakes passed
> on retry).

Follow-up to `README.md` finding #2 ("planning that same shape is exponential in
the number of unioned partitions"). Finding #1 — the CTE cycle — is **fixed**
and out of scope here; see the bottom of this file.

The cause is now located exactly. This is a single function doing a Cartesian
product. Everything below is measured on the working tree of
`C:\Users\ethan\coding_projects\pytrilogy` (branch `more-cloud-work`), Python
3.13, DuckDB dialect.

---

## The hot spot

```
trilogy/core/processing/node_generators/select_helpers/datasource_injection.py:107
    for combo in product(*[by_value[v] for v in values]):
```

in `_best_enum_union`. 97% of total plan+render wall time is inside this one
function (`cProfile`, cumulative, n=11):

| function | ncalls | cumtime |
|---|---|---|
| `_best_enum_union` | 4 | 29.68 s |
| `_datasource_score` | 7,794,468 | 23.40 s |
| `Datasource.is_file` | 7,794,547 | 18.02 s |
| `enum.__hash__` | 46,863,293 | 10.43 s |

Total run was 30.6 s. The 7.79 M `_datasource_score` calls are exactly
`177,147 combos x 11 members x 4 calls`.

## Why it is exponential

`_best_enum_union` enumerates **one source per enum value**, as a full Cartesian
product, then scores each combo. For an enum-partitioned key with `V` values and
`k` candidate sources per value the loop runs `k^V` times, each iteration costing
`O(V)` scoring plus `O(V x columns)` set intersections.

In this model `city` is a `V`-value enum and every city contributes `k = 3`
sources bound `complete where city = 'X'` (raw municipal, raw community, and the
published per-city parquet), so the count is `3^V`, and `get_union_sources` is
reached 4 times per plan with identical arguments.

Measured (instrumented combo counts, no caching):

| cities | combos enumerated | plan + render |
|---|---|---|
| 8 | 26,244 | 0.5 s |
| 10 | 236,196 | 3.1 s |
| 11 | 708,588 | 8.3 s |
| 12 | 2,125,764 | 31.0 s |

That is the reporter's "x2.8 per additional city" — it is literally `x3`, the
per-value candidate count. The real 14-city model is worse than `3^14` because
Boston contributes 6 sources (4 municipal partitions + community + published),
which is why it costs ~150 s to plan where the synthetic costs ~90 s.

## Reproducing

```bash
cd C:\Users\ethan\coding_projects\sf_tree_reporting\upstream_repro\cte_cycle
C:\Users\ethan\coding_projects\pytrilogy\.venv\Scripts\python.exe generate.py 8 10 11 12
```

`generate.py` builds an N-city copy of the shape from local CSVs (no GCS, no
credentials) and plans a dry-run refresh of the merged datasource. Sizes above 12
are slow enough to be annoying; 8→11 is enough to see the law.

To see the combo counts and the profile directly, the two throwaway scripts used
for the numbers above are described in "Instrumentation" at the bottom.

---

## Two tiers of fix

### Tier 1 — constant factor, low risk (~10x, does not change the exponent)

Both of these are pure caching and cannot change which combos are selected:

1. **Memoize `_datasource_score` per datasource.** It is a pure function of
   `ds.address` and is currently recomputed `k^V x V` times over ~`3V` distinct
   datasources. `Datasource.is_file` and the `AddressType` enum comparisons
   underneath it are where the 46 M `enum.__hash__` calls come from.
   *Measured*: n=12 drops 31.0 s → 12.3 s; n=11 8.3 s → 4.0 s.
2. **Memoize `_best_enum_union` / `get_union_sources` across the 4 identical
   calls per plan.** `get_union_sources` is invoked from three call sites
   (`select_merge_node.py:167`, `network_build.py:293`,
   `source_planning.py:284`); in this plan it reaches `_best_enum_union` 4 times
   with identical `(dses, enum_type, merge_key)`. Worth a further ~4x. Note
   `describe_incomplete_partitions` (same file) calls `_best_enum_union` again on
   the error path, so a cache helps failure messages too.

Together this buys roughly 3 more cities before hitting the same wall. It is not
a fix — at `x3` per city, a 10x constant is 2 cities of headroom — but it is
cheap, safe, and independently worth landing.

### Tier 2 — remove the exponent (the actual fix)

The loop's objective decomposes almost completely, and that is the opening:

- `score = sum(_datasource_score(ds) for ds in combo_list)` is **separable** —
  per-value independent.
- `signature = intersection of member column sets, minus the merge key` is what
  **couples** the choices, and it is the only thing that does.

So instead of enumerating combos and deriving signatures, enumerate **candidate
signatures** and derive the combo:

> For a target signature `S`, a value `v` is satisfiable iff some `ds` in
> `by_value[v]` has `columns ⊇ S`; among those pick the max-scoring one (the
> score is separable, so per-value argmax is globally optimal for that `S`).

That turns `k^V` into `O(|candidate signatures| x V x k)`. The open design
question — the part that needs care, not just typing — is enumerating candidate
signatures completely. The distinct column-sets of the ~`3V` candidate
datasources are the obvious seeds, but a maximal signature can be a strict
*intersection* of two members' column sets and equal to neither (value1 has
`{a,b}`, value2 has `{b,c}` → signature `{b}`), so seeds alone are not complete.
Closing under pairwise intersection is the natural next step; whether that
closure stays small on real models needs checking against the fixtures below
before committing to it.

Given how few distinct column-sets real models have, an equivalent and simpler
framing may be to **collapse each value's candidates by column-set first** (keep
only the max-scoring datasource per distinct column-set within a value) and then
run the existing product over the collapsed lists. That is a strictly smaller
product with identical output, and it is trivially correct. It does **not** help
this particular model — each city's 3 sources have 3 different column sets — but
it is a safe first step that composes with the signature enumeration.

## Correctness invariants any rewrite must preserve

These are load-bearing and each is pinned by a real query. Read the comments in
`_best_enum_union` before touching it:

1. **One combo per distinct overlap signature, not one global best.** Parallel
   partitionings keyed by the same enum (sales vs. returns vs. dim, all on
   `channel`) must each contribute their own union datasource instead of
   collapsing into the single highest-scoring one.
2. **Mixed-family combos are legitimate.** Members may disagree on intrinsic
   (`~`) partiality of a shared column (q05: web_sales + catalog/store_returns).
   Do not reject them. Union partial propagation is what keeps them from
   outranking a pure family (q14).
3. **Only maximal signatures survive.** A signature that is a strict subset of
   another's is dropped (`sigs`/`maximal`, lines 140-142). This is what filters
   "2 sales + 1 dim" combos out.
4. **Materialization ranking.** table (2) > static file (1) > script/query (0).
   Any per-value argmax must use the same ordering, and ties must stay
   deterministic — plan shape must not vary with dict/set iteration order.

## Verification

Targeted:

```bash
.venv\Scripts\python.exe -m pytest tests/engine/test_enum_unions.py \
    tests/generators/test_datasource_scoring.py \
    tests/complex/test_dataset_merge.py tests/modeling/ncaa/test_ncaa.py -q
```

Then the full sweep, because this function decides *which physical tables a
query reads* — a wrong combo is a silently wrong answer, not a crash:

```bash
.venv\Scripts\python.exe -m pytest tests/modeling/tpc_ds_duckdb -q -p no:randomly
.venv\Scripts\python.exe -m pytest tests -q -p no:randomly -m "not adventureworks_execution" -k "not clickhouse_server"
```

Baseline on the current tree (with the cycle fix in place): TPC-DS 172 passed
(~114 s); full suite 7963 passed, 41 skipped (~15 min). TPC-DS benchmark
artifacts under `tests/modeling/tpc_ds_duckdb/` always show up dirty after a run
— that is expected churn, not a regression.

Success criterion: `generate.py 14` well under a second of union selection, and
the growth curve flat rather than `x3` per city. The reporter's job budget is
3600 s and they expect a 16th city.

## Instrumentation used for the numbers here

Two throwaway scripts (not committed):

- **Profile**: build the model via `generate.write_model(N)`, then
  `cProfile` around `create_refresh_plan` + `execute_refresh_plan(dry_run=True)`,
  sorted by `cumulative` and `tottime`.
- **Combo counter**: monkeypatch
  `datasource_injection._best_enum_union` with a wrapper that rebuilds `by_value`
  the same way the real one does and records
  `(merge_key.address, len(by_value), sorted candidate counts, math.prod(...))`
  before delegating. Monkeypatching
  `datasource_injection._datasource_score` with an `id(ds)`-keyed cache is how
  the Tier-1 numbers were measured without touching the tree.

---

## Out of scope: the cycle (finding #1) is fixed

`ValueError: CTE dependency graph contains a cycle` was `UnionDimPushdown`
pushing a "dim" into a `UnionCTE`'s branches when that dim was itself a
single-key projection **of that union** — `branch.add_dependency(dim_cte)` closed
`union -> dim -> union`. Fixed in
`trilogy/core/optimizations/union_dim_pushdown.py` by having
`_resolve_push_context` refuse a dim CTE that is, or transitively reads from, the
container. Regression tests in
`tests/optimization/test_union_dim_pushdown.py`
(`test_union_dim_pushdown_refuses_dim_derived_from_the_union` and
`..._plain_refuses_dim_derived_from_the_target`).

Note the two findings are **unrelated in mechanism** but share a trigger: both
are provoked by many `complete where`-partitioned sources feeding one merged
datasource. Fixing this one will not affect the cycle, and vice versa.

There is a third, still-open observation from the same investigation, being
looked at separately: the planner emits an INNER join from the union to a
single-key projection of itself, which is a semantic no-op. The cycle fix makes
that join *correct*; it does not make it *free*.
