# Map rendering audit — 2026-10-02

Branch: `codex/map-rendering-audit`, based on `codex/unified-tree-crowns`.

Before opening the PR, rebased onto main including #111, which independently
fixed the custom-sprite intro lookup and allowed sprite queries during the tile
pause. Preserve its renderer accessor, intro regression test and CI settings;
this PR additionally consolidates the sequential readiness waits.

The reported popping has several plausible, reproducible causes in the local
rendering path. Ordinary panning does not intentionally reload the city parquet.
Heatmaps/circles use DuckDB-generated vector tiles; desktop trees use a separate
bounded SQL query and custom WebGL batch. These share the city tables, but do
different query and geometry work.

## Fixed

| Finding | Effect | Change |
| --- | --- | --- |
| Sprite queries covered exactly the viewport | Newly exposed trees waited for a later query; offscreen anchors could be removed while their silhouettes remained visible | Query a 30% margin on each side and reuse complete batches until the remaining margin reaches 10%. Prioritize visible trees over buffered trees when the cap fills. |
| A delayed camera query could replace the current batch after a large pan | Old-view trees could pop back in, then disappear on the next response | Reject responses whose coverage no longer contains the current viewport. Keep the previous batch while catching up. |
| Every loaded `trees` source event scheduled another independent sprite query | Tile arrivals repeated SQL, sorting, allocation and GPU uploads with no camera or filter change | Sprite refreshes now follow camera movement and explicit invalidation. |
| Dense batch construction used a growing JavaScript number array, copied it into float32, and projected every tree into a keyed map for sorting | Main-thread allocation and preparation could interrupt frames | Allocate the final float32 buffer directly; sort along the camera's ground-plane depth axis. Rotation/pitch checks verify the order. |
| Query failure cleared the visible batch | Temporary worker errors blanked trees | Preserve the previous batch on a failed replacement. Explicit city/filter invalidation still clears obsolete data. |
| Both `zoom` and `move` called the same viewport updater | Wheel zoom duplicated bounds calculations and worker messages | Subscribe to `move`, which also covers zoom. |
| Color-only layer updates invalidated sprites, and ordinary filters rebuilt the default city-wide color/aggregate tables | Extra clear/reload cycles and SQL during filter changes | Keep visual style updates separate from data invalidation; skip rebuilding an already-default color map. |
| Legacy category/color icons were still registered | Generated unused raster assets alongside the custom atlas | Remove legacy registration and missing-image recovery from the main map. |
| Intro readiness queried the custom layer using MapLibre's vector feature index | Two five-second waits and a six-second fallback could all time out; the tile pause also prevented sprites from loading | Check the custom renderer's projected tree anchors, use one bounded readiness wait, and allow its local SQL while vector auto-fetch is paused. |
| Source refresh code called an optional `reload()` method | Misleading refresh plumbing | The installed MapLibre vector source has `setTiles()`, which reloads itself, but no public `reload()`. Remove the ineffective guards and duplicate intro invalidation. This was not evidence of two network loads. |

## Measurements and verification

Deterministic Chromium fixture, 1100×800 viewport, same machine, one before/after
run. The dense case prepares 32,768 synthetic trees; median excludes the first
two of seven iterations. This is CPU batch preparation, not end-to-end FPS or
live SQL latency.

| Measurement | Before | After |
| --- | ---: | ---: |
| Extra sprite queries after five spaced tile-completion events | 5 | 0 |
| Extra sprite queries after five 10-pixel pans | 5 | 0 |
| Median dense-batch preparation | 69.2 ms | 25.0 ms |
| Float32 vertex buffer at the sprite limit | 11,010,048 bytes | 11,010,048 bytes |

Three new browser regressions failed before the fixes: redundant source-event
queries, missing viewport buffer, and acceptance of a departed viewport's
response. The renderer suite also checks query errors, rotation/pitch ordering,
late filter responses, removal, GPU sizing, picking and zoom transitions.

Reproduce the microbenchmark from `src/`:

```powershell
pnpm dev --port 6174
# In a second terminal:
$env:MAP_BENCH_LABEL = 'latest'
pnpm exec node scripts/bench-map-renderer.mjs
```

Results are written to gitignored `src/bench-results/map-renderer-*.json`.
The benchmark deliberately uses the isolated renderer fixture, not remote data.

Validation: production build and lint; the full Vitest suite; the deterministic
WebGL renderer suite; 17 browser checks covering startup city resolution,
themes and desktop/mobile tree cards. The 17-test run used a dedicated e2e build
on port 6175 because port 6173 was already serving a development build without
the e2e fixture seam. That run preceded the final intro-readiness correction;
the final live-map checks exercise that correction separately.

Final live San Francisco checks (desktop and mobile, development server) reached
ready and rendered trees without page errors or failed worker RPCs. Each made
one initialization and one city-context request. Five small pans made no further
parquet requests in either layout. Desktop retained its 242-tree batch without
another sprite query; mobile reached its 4,096-crown limit and made five local
crown queries. Startup did make multiple parquet requests, including repeated
URL/range combinations; this trace does not distinguish metadata probes from
transferred bodies and is not proof of duplicate whole-file downloads.

The final full Vitest rerun passed 260 of 261 tests; the chat-publish integration
test exceeded its existing five-second timeout while other verification jobs
were running. It had passed in the earlier full run and passed its isolated
retry in 1.83 seconds. No timeout or test expectation was changed.

## Remaining limits and follow-up priorities

1. **Dense-view selection can still pop.** The desktop limit is 32,768 sprites;
   mobile crowns stop at 4,096. Nearest-tree selection changes as the camera
   moves. Desktop marker opacity does not fade individual arrivals/departures,
   and circles disappear at zoom 15.5 regardless of sprite readiness. Buffer
   reuse is deliberately disabled when the result hits its limit, since a
   truncated batch cannot promise full coverage. Stable spatial selection or
   a tile-based custom renderer with a readiness-aware circle handoff is the
   next substantial rendering change. A 25 ms dense rebuild still exceeds a
   60 Hz frame budget.
2. **Tile cancellation and refresh state need a coordinated worker change.**
   The protocol ignores MapLibre's abort controller, so obsolete requests can
   remain in the three-worker queue. Tile/batch requests are deduplicated and
   cached, but queue entries themselves are not cancelled. Query, color and ID
   filters are separate asynchronous RPCs; rapid publishes can interleave.
   Cache invalidation clears in-flight registries without cancelling old work,
   and not every invalidation advances the revision. An atomic configuration
   operation with an epoch carried through requests and cache writes would
   address these races. These are code-audit findings, not reproduced causes
   of an ordinary steady-state pan in this audit.
3. **Hit testing is linear.** Desktop hover calls `pick()` on mouse movement and
   can scan the entire sprite batch. Add a screen-space hit index or throttle
   hover work if dense views remain sluggish after the query fixes.
4. **Cold startup still loads a city.** DuckDB materializes city trees and
   `trees_fast`, prepares aggregate/geometry caches, loads shared enrichment,
   and prepares crown predictions before readiness. Near Me is not a spatially
   partitioned download. `loadCityTrees()` skips an already-loaded city;
   `setCityContext()` still rebuilds derived state on subsequent calls.
5. **Startup assets are large.** The audited production build's main JS chunk
   was approximately 7.69 MB / 2.05 MB gzip, plus the selected DuckDB WASM asset.
   Routes eagerly import all views. Splitting dashboard/editor code from map
   entry is worth measuring independently of panning changes.
6. **Tile edges deserve a dedicated continuity fixture.** Detailed MVT paths
   assign points to their owning tile even though geometry encoding accepts a
   buffer. Large circles near tile boundaries may need neighbor duplication.
   This remains a hypothesis requiring pixel-level boundary checks, especially
   on mobile; it was not changed here.

The audit covered main map lifecycle, intro, custom rendering/picking, worker
queries/caches, source refreshes, theme preservation, and the separate dashboard
dot-map path. It does not establish performance across every city, GPU, mobile
device or throttled network. No data publication or infrastructure changed.
