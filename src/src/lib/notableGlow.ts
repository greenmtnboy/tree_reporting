/**
 * How a notable tree (a proper name or a significance blurb; the block
 * column `notable`) stands out on the map. One look on all three renderers:
 * the desktop's dots (treeDotsLayer.ts), its sprites (treeCrownLayer.ts) and
 * the phone's vector tiles (useMapLayers.ts). See
 * docs/NAMED_TREES_AND_CROWN_POSITION.md.
 */

/** The halo's colour: a warm gold that reads on the light and dark basemaps and is in no colour scheme. */
export const NOTABLE_GLOW_COLOR = '#ffd54a'
/** The same, as a GLSL vec3 body. */
export const NOTABLE_GLOW_RGB = '1.0, 0.835, 0.290'
/** The halo's width past a dot's disc at least, CSS pixels; a wide crown's grows with it. */
export const NOTABLE_GLOW_PX = 6
/** The halo's opacity at the disc's edge; it fades to nothing at its own. */
export const NOTABLE_GLOW_ALPHA = 0.6
/** A desktop sprite's halo: its quad drawn again this much wider and taller, under it. */
export const NOTABLE_HALO_SCALE = 1.45
