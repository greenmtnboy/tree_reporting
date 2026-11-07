# Handoff: remaining pytrilogy summary misses (part 2)

Updated 2026-11-01. Part 1 is done: pytrilogy 0.3.439 fixed #836 (city
builds), #834 (nested aggregates) and #835 (null-key row), and it's live on
the production resolver (trilogy-studio-core #281). Those issues are closed.

**Your job:** get the remaining dashboard default queries served from the
summaries, by pushing pytrilogy #827 forward. 57 of the 73 default queries
(Boston + all cities) are served now. Every miss is a correct answer that
reads the city's tree file instead of a summary, so they cost phone memory,
not correctness.

## Rules

- Never work around a planner bug in a query or model. Put repros in
  `upstream_repro/` (gitignored) and file them upstream.
- A follow-up to a CLOSED issue gets a new issue that links it. Comment
  freely on open ones.
- Use a git worktree; another agent works in the main checkout. Commit, push
  and PR only when the user asks. pnpm only under `src/`.
- Never print or commit the HMAC keys in the main checkout's `.env`.
- When a fix ships, bump trilogy-studio-core `pyserver`'s exact pin
  (`requirements.txt` and `pyproject.toml`, as in #281) only after the
  checks below pass on that release.

## The misses (#827)

| Queries | Shape | Repro |
|---|---|---|
| `species:selector-*` (4 pickers) | G2/G3: the same column selected twice | `upstream_repro/summary_misses_0434/repro.py` (toy `m/`) |
| `species:sp-top-species` | K3: a ratio over the total beside `coalesce(common_names[1], species)` | same |
| `summary:local-native-share`, `species:sp-biome-fit` | filtered aggregates `count(x ? cond)`; `count_distinct(key)` | `upstream_repro/native_by_species/shares.py` |
| `species:context` (under a city) | a key plus a property, no aggregate, under a partition filter | `native_by_species/context.py` |
| `species:sp-age-distribution` | the trees answer this on purpose since #839: the summary answer was n². Check with upstream whether a correct summary plan is possible. | — |

The issue comments that list these:
https://github.com/trilogy-data/pytrilogy/issues/827#issuecomment-6557672026
and #issuecomment-6559400228.

Also open, not blocking: #820 (a CASE over a species-grain aggregate is a
keyless join) and #817 (import order; probably fixed by #822, so re-check
and close it with a note).

Upstream's #839 added a frozen copy of our models and dashboard catalog to
pytrilogy (`tests/modeling/tree_reporting`). It checks every summary-served
answer against the tree rows. If our models change materially, offer them a
refresh.

## Checking a pytrilogy build

- Install a wheel:
  `uv pip install --target C:/tmp/ptX --no-deps --python 3.13 pytrilogy==X`.
  For main, use `"pytrilogy @ git+https://github.com/trilogy-data/pytrilogy@<sha>"`.
- Run the toy repros with
  `PYTHONPATH=C:/tmp/ptX /c/tmp/pt428venv/Scripts/python.exe <script>`.
- Run a local resolver (`local-resolver-e2e-recipe` memory):
  `cd trilogy-studio-core/pyserver && PYTHONPATH=C:/tmp/ptX .venv/Scripts/python.exe -m uvicorn main:app --port 5693`.
  Then, in `src/`, run `TRILOGY_RESOLVER_URL=http://127.0.0.1:5693 pnpm test`
  and `pnpm test:queries`.
- Run the coverage probe: copy
  `upstream_repro/rollup_default_views/zz-rollup-probe.test.ts` into
  `src/src/tests/`, run it with `PROBE_ROLLUP=0`, and read `probe.txt`.
  Delete the copy afterwards. Results so far are in `C:\tmp\probe434\`
  (`native439.txt` is 57/73).
- Compare answers between versions with
  `upstream_repro/rollup_default_views/answers_diff.py`, using the probe's
  `PROBE_SQL` dumps. This is how the 0.3.430 age-chart n² bug was found.
- Run the data tests from `data/raw`, with the command in `AGENTS.md`.
