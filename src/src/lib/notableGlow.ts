/**
 * How a notable tree (a proper name or a significance blurb; the block
 * column `notable`) stands out on the map. One look on all three renderers:
 * the desktop's dots (treeDotsLayer.ts), its sprites (treeCrownLayer.ts) and
 * the phone's vector tiles (useMapLayers.ts). See
 * docs/NAMED_TREES_AND_CROWN_POSITION.md.
 */

/**
 * The halo's colour: a sky blue. No colour scheme uses it, and against
 * foliage greens, autumn golds and grey it is the one hue that never blends.
 */
export const NOTABLE_GLOW_COLOR = '#38b6ff'
/** The same, as a GLSL vec3 body. */
export const NOTABLE_GLOW_RGB = '0.220, 0.714, 1.000'
/** A desktop sprite's outline, a deeper blue so the silhouette's edge reads on a light basemap. */
export const NOTABLE_OUTLINE_RGB = '0.090, 0.478, 0.890'
/** The halo's width past a dot's disc at least, CSS pixels; a wide crown's grows with it. */
export const NOTABLE_GLOW_PX = 6
/** The halo's opacity at the disc's edge; it fades to nothing at its own. */
export const NOTABLE_GLOW_ALPHA = 0.6
/** A desktop sprite's halo: its quad drawn again this much wider and taller, under it. */
export const NOTABLE_HALO_SCALE = 1.45
/**
 * A desktop sprite's outline width, in the atlas's distance units (0.5 is the
 * silhouette's edge; 0 is DISTANCE_RANGE outside it, 8 of 192 texels): about
 * 3 texels, 2 to 3 pixels on a sprite near the camera.
 */
export const NOTABLE_OUTLINE_WIDTH = 0.2
