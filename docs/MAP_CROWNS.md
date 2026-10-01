# Tree sprites and close-range crowns

Desktop has one `trees-icon` custom layer, from its first appearance at zoom
14.4 through maximum zoom. The same distance-field sprite scales continuously
with zoom and camera distance. There is no native-symbol fallback, feature-state
hide/show handoff, second icon layer, or delayed growth animation. Height is
proportional to crown width; the shader keeps separate dimensions for a future
height prediction.

Mobile uses ground-plane rings in `trees-crown`, fading in from zoom 16.5 to 18.
Solid outlines mean recorded widths; broken outlines mean published predictions.
Desktop does not draw rings. Ground dots and the overview heatmap still provide
the earlier level of detail on both layouts.

## Data readiness

`setCityContext` prepares a city-scoped projection of `tree_id`, `city` and
`predicted_crown_width_m` from the published predictions parquet **before**
opening the city-readiness gate. The map remains in its existing loading state
until this finishes. Thus `getMapTrees` returns a complete sizing snapshot from
the first batch. This adds prediction loading to initial city readiness rather
than allowing a late measurement to resize already-visible trees.

Positive finite recorded `trees_fast.crown_width_m` wins; otherwise a valid
published prediction is used. Values above 60 m are rejected. Missing widths
remain null and use the marker baseline; no DBH default or mature species spread
is presented as a crown measurement. Missing/unavailable predictions resolve to
recorded-only sizing for the worker session. The result, including failure, is
cached per city; reload to retry. No datasource is added to the Trilogy bundle,
and the published data models are unchanged. Predictions retain their upstream
curation limitations; these are not urban-calibrated or density-adjusted.

## Query and rendering bounds

`crownQuery.ts` joins the effective map query, published ID filter and display
color map. Desktop queries the geographic viewport, keeping at most 32,768
nearest markers including distant trees and trees without crown widths. Wrapped
longitude bounds are supported. Mobile queries only the camera neighborhood and
returns at most 4,096 valid crowns.

Physical crown sizing is limited independently of viewport depth: it is fully
available within 700 m of the actual eye position and fades to ordinary marker
sizing by 1,800 m, including eye altitude. Only the nearest 4,096 eligible crowns
can acquire physical size; the outer density-budget band fades smoothly. These
limits are evaluated by the shader every frame, including while a query is
pending. Distant desktop trees stay in the same layer as small markers. A flat,
highly pitched viewport cannot increase the marker or crown budgets.

The zoom blend runs continuously from 14.4 to 19.5 using geometric interpolation.
A new batch already contains widths and uses the current camera scale on its
first frame. The sprite query is independent of vector tile completion: a distant
basemap/tree tile cannot delay nearby desktop sprites. Refreshes are throttled
to 250 ms, with one request in flight. Filter, color, source and city revisions
invalidate old batches and outstanding responses. Removing the layer releases
its GPU resources, timers and listeners; context restoration recreates resources.

## Artwork and cost

`treeArtwork.ts` defines normalized vector shapes for all nine forms. Native
canvas artwork remains available for artwork previews; the desktop map uses a
single cached 600 × 600 RGBA distance atlas (about 1.37 MiB), with 192 samples per
shape and four-pixel gutters. RGB stores separate distances for trunks, foliage
and blooms. One texture lookup reconstructs antialiased coverage in screen space,
then composites brown trunks, arbitrary foliage colors and pink blooms.

The shapes include tapered stems with shallow elliptical feet, branching crowns,
swept conifer tiers, a curved palm, clustered flowers and cascading weeping
foliage. Detail is baked into the atlas: no additional per-frame geometry,
texture samples or shader work. Vector canopy extents exclude transparent
padding when converting measured crown widths to sprite size.

Desktop submits one draw of at most 196,608 vertices (six per sprite), using at
most a 10.5 MiB vertex buffer. Mobile submits at most 24,576 vertices / 1.3125 MiB.
Camera motion changes uniforms; it does not regenerate artwork or geometry each
frame. Query refreshes rebuild the bounded batch, sorted back-to-front. Both
layouts use a local coordinate origin to avoid street-level float32 jitter.
Silhouette hit testing uses the same camera/size equations as the desktop shader.

Distance fields retain finite sampling precision at extreme magnification,
particularly at sharp corners. Old WebGL1 devices without derivatives use a
fixed edge transition. An SVG uploaded as an ordinary map icon would still be
rasterized; see [the distance-field explanation](https://docs.mapbox.com/help/dive-deeper/using-recolorable-images-in-mapbox-maps/).

The camera accessor and custom matrix use the pinned MapLibre 4.x API. Recheck
`getCameraPosition`, `customLayerMatrix`, and the custom-layer implementation
wrapper on a MapLibre upgrade.

## Verification

Run `pnpm test:crowns` from `src/`. The deterministic WebGL suite checks physical
size, sharp enlarged edges, picking, all nine materials, one draw/texture, delayed
first loads, continuity through the former zoom-16 handoff, distance limits,
filter invalidation, stale replies and resource cleanup. It also runs in CI.
`crownQuery.test.ts` checks measurement precedence, missing widths, viewport and
camera bounds, filtering, latitude correction and both population budgets.

For visual review, open `/renderer-tests/crowns.html` on the dev server and choose
**All tree shapes**. It compares enlarged sprites with 24 px and 48 px artwork.
The fixture is excluded from the production entry points.
