# Handoff: refresh planning rebuilds the build environment once per statement

> **RESOLVED 2026-08-17** on `pytrilogy` branch `more-cloud-work`. All tiers
> implemented: A (shared `execution/state/isolation.py::hidden_datasources`,
> counter snapshot/restore guarded by an in-window-write check), B (ephemeral
> parse — `parse_text(..., ephemeral=True)` + `Executor.execute_ephemeral`, so
> probe aliases never commit), C (batched MAX probes with per-concept
> fallback), plus a multi-generation `_SESSION_CACHE_STORE` (stamp→bundles LRU,
> cap 4) so the full-env and hidden-env bundles coexist. Note: B turned out to
> be correctness-load-bearing, not optional — with counter restore alone, two
> same-membership windows committing different `max_value` lineages produce
> identical stamps over different content (bundle poisoning). Measured:
> `materialize_baseline` exactly 2× per plan+dry-run (was 14 at n=12);
> generate.py 0.2/0.4/0.6s at n=12/16/24 (was 0.6/0.8/2.0), n=48 in 2.6s.
> Mechanism pinned by `tests/execution/state/test_planning_cache_stability.py`.
> Channel 3 (status flips) left as-is — the multi-stamp store makes it a
> cache-key change, not an eviction. Full sweep green.

Follow-up to `PERF_HANDOFF.md` (the `k^V` enum-union exponent — **fixed** 2026-08-17)
and `README.md` finding #1 (the CTE cycle — also fixed). This is the remaining cost:
refresh planning is now polynomial but ~O(N²) in city count, because every planned
statement evicts and rebuilds the session build-environment cache. Goal: planning on
the order of milliseconds; a refresh plan should materialize the build environment
**once or twice**, not once per statement.

All numbers measured on `pytrilogy` branch `more-cloud-work` (post enum-union fix),
Python 3.13, DuckDB, Windows. Repro is this directory's `generate.py`.

---

## Current numbers

`generate.py 12 14 16 20 24` (plan + dry-run render, parse excluded):

| cities | plan+render |
|---|---|
| 12 | 0.6 s |
| 14 | 0.6 s |
| 16 | 0.8 s |
| 20 | 1.3 s |
| 24 | 1.9 s |

cProfile at n=24 (4.7 s under profiler): 26 `process_query` calls (≈one per
refresh-plan statement), and **26 `materialize_baseline` → `build_environment_recorded`
calls — 2.4 s cumulative, ~half the total** (2,545 `_build_datasource`, 2,700
`BuildDatasource.__init__` at 2.1 s cum, `_validate_scoped_join_endpoint_identity`
0.6 s). Statements scale with N and each environment build scales with N → N².
The other half is per-statement v4 discovery (`_plan_query_node` ~1.1 s cum), which
is genuine per-query work; caching the baseline attacks the first half only, so
expect ~2× from Fix A/B alone and much more from Fix C (fewer statements).

## The mechanism — a good cache already exists and is evicted every statement

`trilogy/core/query_processor.py`:

- `_session_build_caches` (~line 886): a module-level `_SESSION_CACHE_STORE` keyed by
  `id(environment)` holds `BuildCaches` bundles (build caches, pseudonym map, and
  `env_baselines`) per scoped-join set. Guarded by a **mutation stamp**:
  `(concepts.content_version, datasources.content_version, concepts.mutations if
  overlays live else -1, len(alias_origin_lookup), sorted (ds_id, status) tuple)`.
  Stamp change ⇒ the whole bundle store for that env is dropped.
- `get_query_node` (~line 979): `caches.env_baselines[materialize_join_key]` caches the
  expensive `materialize_baseline()`; on hit, a statement only pays the cheap
  `materialize_delta` overlay. `nested_select.py:171-184` shares the same cache.

So the design intent is exactly "build once, reuse across statements" — measured
reality (instrumented `_session_build_caches` at n=12): **13 stamp changes during
`create_refresh_plan` + 1 during `execute_refresh_plan` ⇒ 14 full baseline builds.**
Every statement saw `concepts.content_version +1` and `datasources.content_version
+14` relative to the previous one.

## The eviction channels, exactly located

Instrumentation: wrap `EnvironmentConceptDict.__setitem__/__delitem__` and the
datasource dict's `__setitem__/__delitem__/update/pop`
(`trilogy/core/models/datasource.py:505-524` — note `update` and `pop` bump
`content_version` **unconditionally**, no identity check), record call stacks when
`content_version` changes on the session env. Results at n=6 (7 probe statements):

### Channel 1 — hide-by-pop of non-root datasources (the +14/statement)

`watermarks.py::get_concept_max_watermark_abstract` (trilogy/execution/state/
watermarks.py:390-407) computes each derived concept's expected MAX by **popping
every non-root datasource out of `executor.environment.datasources`, planning/running
`SELECT MAX({concept}) as max_value;`, then `update()`-restoring them** in a
`finally`. At n=6 that is 7 pops + 1 update-restore per probe, 49 pops total. The
restore is object-identical, but `pop` and `update` bump `content_version`
unconditionally — so the stamp never returns to its pre-probe value and the bundle
(including all baselines) is evicted for every probe.

The same hide-by-pop pattern exists in **two more sites** — any fix must cover all
three (grep `datasources\.pop` under `trilogy/execution/state/`):
- `partitions.py:513-533` (`probe_expected_partitions` — same "only roots may
  answer" contract),
- `state_store.py:995-1013` (`execute_refresh_plan` hiding not-yet-refreshed SQL
  assets while running each asset).

`trilogy/execution/state/AGENTS.md` documents this pattern as a known "iteration
hazard" — it is an established contract ("hides non-root datasources for the
duration of its query"), not an accident. The *hiding* is required; the *stamp
damage* is the bug.

### Channel 2 — the probe's `max_value` alias registration (the +1/statement)

Each probe's `executor.execute_query("SELECT MAX(x) as max_value;")` parse registers
`local.max_value` into the **session** environment via
`semantic_state.commit → environment.add_concept` (environment.py:1493). A fresh
Concept object per parse, so the identity check in
`EnvironmentConceptDict.__setitem__` (environment.py:353-357, bumps unless
`data.get(key) is not item`) sees real content change. Note: probes for *different*
concepts produce genuinely different `max_value` lineages, so an equality-based bump
would still evict once per distinct probe — this channel needs the alias to not
land in the durable env (or a stable per-concept alias), not a smarter comparison.

### Channel 3 — persist status flips (minor, but a decision to make)

The persist marks datasources `PUBLISHED` (an in-place status flip captured by the
stamp's status tuple) — one more eviction at execute time. Product guidance from the
session that produced this handoff: **a persist does not execute in the plan
context at all, so its status flip probably should not evict planning caches
mid-plan.** Verify before acting: check whether anything in statement planning
actually reads `Datasource.status` such that a mid-plan flip must change plan
output; if nothing does, drop status from the stamp (or scope it), with a comment
explaining why. If something does, leave it — it is one eviction, not N.

## Fix directions (tiered; A+B are the core, C is the structural win)

### A. Make hide-restore stamp-neutral — without letting the hidden window lie

The env content after `finally: datasources.update(hidden)` is *identical* to the
content before the pop, so the honest stamp is the original one. Two shapes:

1. **Counter snapshot/restore around the hide window**: record
   `(mutations, content_version)` before the pops, restore them after the
   restoring `update()`. Simple, local to the three sites (extract a shared
   context manager, e.g. `hidden_datasources(executor.environment, keep=root_assets)`,
   so the three copies can't drift).
2. **A first-class hidden-set** on the datasource dict that iteration/materialization
   respects, so hiding never touches the dict at all. Cleaner but much wider blast
   radius — every consumer of `environment.datasources` (Factory, graph build,
   validation, the state code itself) must honor it. Probably not worth it unless
   (1) proves insufficient.

**The critical trap in (1)**: the probe *plans queries inside the hidden window*,
and those plans MUST NOT reuse the full-env bundle — a baseline materialized with
all datasources visible would let the probe read through the very sources it hid
(silently wrong watermarks, the same "wrong physical table" severity class as the
enum-union work). Today that isolation is provided *accidentally by the eviction
you are removing*. Restore the counters **after** the window closes, never before;
during the window the popped counters give the hidden state its own distinct stamp
and its own bundle. Bonus: if consecutive probes hide the same set and channel 2 is
also fixed, every probe window shares ONE hidden-state bundle — the ideal end state
is exactly two baselines per refresh plan (full env + hidden env).

Also do NOT "fix" this by comparing dict content in the stamp (hashing datasources
per call) — the stamp is read on every statement and must stay O(N) trivial.
And per repo rules: fix at source, no belt-and-suspenders re-checks downstream.

### B. Stop the probe alias from mutating the durable env

Options, in rough order of preference — pick after reading how
`semantic_state.commit` decides what lands in `env.concepts`:
- Run the probe statement so its select alias stays in statement-local scope
  (`local_concepts`) instead of committing to the durable dict — if there is an
  existing executor/parse pathway for "throwaway statement", use it.
- Give the probe a per-concept stable alias (e.g. derive the alias from the concept
  address) AND memoize the parsed statement per address so the re-parse re-registers
  the *identical object* (identity check then keeps the stamp still). The docstring
  at environment.py:260-264 says identical-object rewrites are exactly the intended
  stamp-neutral case.
- Do NOT run probes under a concept overlay: the stamp deliberately falls back to
  the raw `mutations` counter while overlays are live (`has_overlays` branch),
  which evicts *more*, not less.

### C. Batch the watermark probes (structural, biggest win)

`_ensure_concept_max_watermarks` (state_store.py:406) loops derived concepts and
plans **one statement each**. All probes in a loop hide the same non-root set, so a
single `SELECT MAX(c1) as m1, MAX(c2) as m2, ... ;` planned once would replace N
plans with 1 — this kills the N× statement multiplier itself and pays off even
after A+B. Caveat that must be preserved: today each probe absorbs
`UNRESOLVABLE_ERRORS` **per concept** ("a concept the roots cannot answer yields a
null value, not an exception" — load-bearing per the docstring and
`trilogy/execution/state/AGENTS.md`). A batched probe fails as a unit, so on
planning failure fall back to per-concept probes (or bisect). Grain also matters:
all these maxes are grain-() scalars so one combined select should plan cleanly,
but verify the combined statement doesn't force a join between unrelated root
families that individually planned fine.

### Explicitly not the fix

- Weakening stamp semantics generally (equality-based bumps everywhere, hashing
  content) — the counters guard *correctness* of every cross-statement cache.
- Caching `materialize_baseline` output across *genuine* env changes.
- Rust: nothing here is a tight loop; it is Python object construction driven by
  redundant invalidation.

## Correctness invariants

1. **A stale build environment is a silently wrong answer** — the baseline decides
   which datasources/concepts exist for planning. Any stamp-neutrality change must
   be provably "content restored ⇒ stamp restored", never "probably unchanged".
2. **Hidden-window isolation** (see the trap under A): probe planning must see a
   stamp ≠ the full env's stamp for the whole window.
3. Parallel evaluation: managed nodes evaluate on threads (see
   `trilogy/execution/state/AGENTS.md` — `BaseStateStore` carries a lock; the
   ambient store is a *factory* for this reason). The pop/restore pattern is
   already thread-sensitive; a shared context manager must not widen that window.
4. The `-> null, not exception` contract for unanswerable probe concepts (B/C).
5. Watermark keys are concept **addresses**, never names (state AGENTS.md).

## Repro + diagnostics

```bash
cd C:\Users\ethan\coding_projects\sf_tree_reporting\upstream_repro\cte_cycle
C:\Users\ethan\coding_projects\pytrilogy\.venv\Scripts\python.exe generate.py 12 16 24
```

Two throwaway spies (not committed) reproduce every number above:
- **Stamp churn**: wrap `query_processor._session_build_caches`, recompute the
  stamp tuple on entry, print component-wise diffs between consecutive calls; also
  count `Environment.materialize_baseline` calls. Expect (pre-fix) +1 concept /
  +N+2-ish datasource version per statement, 14 baseline builds at n=12.
- **Write sites**: wrap the concept dict's `__setitem__/__delitem__` and the
  datasource dict's `__setitem__/__delitem__/update/pop`
  (`trilogy/core/models/datasource.py:505-524`), filter to the session env
  instance, record `traceback.extract_stack` when `content_version` moved. Pre-fix
  this shows exactly the three channels above and nothing else.

## Success criteria

- `materialize_baseline` runs **≤2×** per `create_refresh_plan` + dry-run
  `execute_refresh_plan` (full env + hidden env), independent of N.
- n=24 plan+render well under 1 s; the n=12→24 slope from statement-planning only.
- With C: statement count per plan is O(1) in probe concepts, not O(N).

## Verification

Planning caches decide what the planner can see — wrong reuse is a silently wrong
answer, so the full ladder applies:

```bash
.venv\Scripts\python.exe -m pytest tests/execution/state -q -p no:randomly
.venv\Scripts\python.exe -m pytest tests/engine/test_enum_unions.py tests/generators/test_datasource_scoring.py tests/complex/test_dataset_merge.py -q
.venv\Scripts\python.exe -m pytest tests/modeling/tpc_ds_duckdb -q -p no:randomly
.venv\Scripts\python.exe -m pytest tests -q -p no:randomly -m "not adventureworks_execution" -k "not clickhouse_server"
```

Baseline on the tree this was written on: TPC-DS 172 passed (~114 s); full sweep
8141 passed / 42 skipped (~21 min; `tests/modeling/faa/test_llm.py` and
`tests/scripts/test_readme_quickstart.py` are network-dependent and flake on GitHub
rate limits — retry before blaming a change). TPC-DS benchmark artifacts under
`tests/modeling/tpc_ds_duckdb/` always show dirty after a run; expected churn.

Also worth adding: a regression test that counts `materialize_baseline` calls
across a small refresh plan (monkeypatch-count, like the spies) — that pins the
*mechanism*, where a wall-clock assertion would only pin the symptom.

## Key files

| what | where |
|---|---|
| session cache + stamp | `trilogy/core/query_processor.py:877-930` (`_SESSION_CACHE_STORE`, `_session_build_caches`) |
| baseline reuse | `trilogy/core/query_processor.py:976-1000`; `trilogy/core/processing/v4_node_generators/nested_select.py:171-184` |
| stamp counter semantics | `trilogy/core/models/environment.py:255-265, 353-361` (concepts); `trilogy/core/models/datasource.py:505-524` (datasources — `update`/`pop` bump unconditionally) |
| materialize entry points | `trilogy/core/models/environment.py:880-956` (`_materialize_factory`, `materialize_baseline`, `materialize_delta`) |
| hide site 1 (watermark probe) | `trilogy/execution/state/watermarks.py:370-412` |
| hide site 2 (partition probe) | `trilogy/execution/state/partitions.py:513-533` |
| hide site 3 (refresh execute) | `trilogy/execution/state/state_store.py:995-1013` |
| probe loop (batching target) | `trilogy/execution/state/state_store.py:406` (`_ensure_concept_max_watermarks`) |
| probe alias commit | `trilogy/core/models/environment.py:1493` (`add_concept`) via `semantic_state.commit` |
| subsystem contracts | `trilogy/execution/state/AGENTS.md` (read it — hide pattern, probe null contract, threading) |
