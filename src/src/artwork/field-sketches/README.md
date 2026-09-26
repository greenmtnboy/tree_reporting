# Field sketches

Editable vector originals for the app's corner illustrations. Each `.svg` is
standalone: open it directly in a browser or import it into a vector editor.
No image generation or editor plugin is needed.

## Review independently

From `src/`, run `pnpm dev:artwork`, or open `/artwork.html` on an existing Vite
dev server. Use `?sketch=woodland` (or another file's stem) to link to a drawing.
This is a separate development entry, excluded from the default production
build. It does not initialize the app, DuckDB, map, analytics, or authentication.

The studio shows the full drawing beside an app-placement approximation, with
light/dark previews, adjustable study visibility, an alignment grid, a panel
overlay switch, and SVG downloads. App placement uses the desktop corner
offsets and opacity; its preview canvas is smaller than an actual app viewport.

## Files

| SVG | Intended placement / motif |
| --- | --- |
| `city.svg` | Upper left: buildings and survey lines |
| `woodland.svg` | Mediterranean woodland: narrow-leaf spray |
| `broadleaf.svg` | Temperate broadleaf forest: lobed leaves and twig |
| `conifer.svg` | Temperate conifer forest: needles and cone |
| `desert.svg` | Desert/xeric scrub: cactus and succulent |
| `grassland.svg` | Grassland/savanna: grasses and seed heads |

`index.ts` registers drawings and their review notes. `FieldSketch.vue` renders
only these trusted, static SVG imports. The app's `FieldBackdrop.vue` chooses
the biome and handles position/opacity. The studio uses the same renderer and
originals, so edits appear in both through Vite's hot reload.

## Generate the city

`city.svg` is generated. Edit `cityScene` in `../generators/city.mjs`, then run
`pnpm artwork:generate` from `src/`. `pnpm artwork:check` verifies that the
committed SVG matches its source. The generator has no dependencies on Vue,
Vite, or a browser, so it can move into the shared artwork package.

Each building has a footprint (`x`, `y`, `width`, `depth`), a height, and a
floor count. All geometry uses the same isometric projection:

- Every wall ends at its footprint's `z=0` street-level corner.
- Roof and wall edges share corners; roofs have no antenna/cross decorations.
- Windows are inset rectangles on the facade planes, inside each floor.
- The tall rear tower and the three foreground buildings are ordered by depth.
- Hidden line segments are cut away behind nearer buildings, preserving a
  transparent background without opaque fills or reusable SVG mask IDs.
- Curb and survey lines use the same ground plane as the buildings.
- Streets have rounded corners, gently bowed runs, paving joints, a zebra
  crossing, dashed lane marks, and a broken second pencil pass. All variation
  is deterministic, so regenerating the same scene produces the same SVG.
- `cityScene.street` sets the corner radius and pocket-garden positions.
  Trees use ground-projected planting rings, trunks with shared branch
  junctions, and softly scalloped crowns. Ground lines are hidden by crowns.

Use separated, non-overlapping footprints; the current depth ordering is
intended for this small composed scene, not interlocking building volumes.

## Generate grassland and savanna

Both biomes share `grassland.svg`, generated from `grasslandScene` in
`../generators/grassland.mjs`. Run the same `pnpm artwork:generate` and
`pnpm artwork:check` commands. Each stalk has a base, quadratic bend, and tip.
Leaf bases are evaluated on that exact curve; leaf outlines rotate into the
curve's local tangent and normal. Seed-head spacing is measured along the
stalk from its tip. Change the head's offset, spacing, sides, width, and length
to iterate without disconnecting leaves from their stalks. The other biome
drawings remain hand-authored.

## Iteration contract

- Preserve the viewBox: biome drawings use `0 0 460 510`; the city uses
  `0 0 520 360`. Keep a transparent background and `currentColor` strokes.
- Use explicit path geometry, with no embedded raster images, scripts,
  external resources, or editor-specific dependencies. Convert generated
  geometry to ordinary SVG paths before saving.
- Group stems, leaf outlines, veins, and pencil details separately when
  refining a drawing. Use shared attachment coordinates for connected parts.
- Check joins at full contrast first. Intentional open-ended construction
  lines are fine; floating leaf bases and disconnected stems need correction.
- Keep secondary detail lighter than the outline. The default stroke is
  1.15 units, pencil detail .65 at .6 opacity, contour lines .7 at .35 opacity,
  and washes use `currentColor` at .08 fill opacity.
- Then check both themes and the app crop. App opacity (.4 for biomes, .34
  for city) belongs in placement, not baked into the whole SVG.
- These are decorative ecosystem motifs, not botanical identification keys.
  Record references and species intent here if moving to species-specific art.

## Current review baseline

Grassland now uses generated attachments and angles. The remaining biome
files preserve the first-pass artwork, including its rough joins. Woodland
leaf bases and conifer needle attachments were drawn independently of their
curved stems; their gaps are not a loading or
draw-animation issue. Low opacity, panel overlays, and cropping also make
parts less visible. Start with `woodland.svg`; per-drawing notes are in the
studio. Use Git history for before/after comparisons rather than duplicating
the artwork into an app copy and a review copy.

The broadleaf upper leaf outline and vein study came from the local Arborary
website's `docs/.vitepress/theme/leaf-studies.ts`; the city drawing follows its
architectural sketch direction. Other geometry was authored for this app.

## Shared-package direction

A future package can serve both Arborary sites with three asset collections:
`leaves/` (species studies), `biomes/`, and `city/`. Export individual SVGs and a
small typed catalog of stable IDs, titles, viewBoxes, species aliases, and
reference/provenance notes. Consumers should own theme colors, opacity,
placement, city-to-biome mapping, and any animation.

Keep the asset/catalog core independent of Vue, Vite, and browser globals.
The current `?raw` imports are a Vite adapter, not a proposed public package
API. An optional Vue entry can wrap the core without making Vue a dependency
for plain SVG consumers. Keep the review studio as a development companion,
outside the published runtime. First migrate the website's existing leaf
studies and aliases without changing their lookup behavior, then use a local
packed tarball in both sites before publishing a versioned release.

This extraction is still app-local; it does not create or publish an npm package.
