/**
 * The shade a species' tree is painted for the time of year.
 *
 * The enrichment table carries three CSS hex columns per species
 * (data/raw/tree_enrichment.preql): `foliage_colors`, the growing-season leaf;
 * `fall_foliage_colors`, the autumn leaf, empty for an evergreen; and
 * `trunk_color`, the bark. The map's default "Leaf shade" colour scheme
 * reads them as a per-species table the worker joins on every colour read
 * (`colorSql` in workers/cityTables.ts), not as a per-tree override: a few
 * thousand rows rather than one per tree in the city. A species with no
 * shade is grey (`UNKNOWN_SHADE_COLOR`).
 *
 * The phase is decided here, from the calendar and the city's hemisphere,
 * not per species: the table has no leaf-out or leaf-drop dates yet. A list
 * with more than one shade is spread across the species' trees by tree id,
 * so a red maple's trees come up red and orange rather than all one hex.
 */

export type FoliagePhase = 'growing' | 'autumn' | 'bare'

/** Northern-hemisphere calendar; the south is six months out. */
const AUTUMN_MONTHS = new Set([9, 10, 11])
const BARE_MONTHS = new Set([12, 1, 2, 3])
/** Inside the tropics there is no autumn: deciduous trees there drop leaves on rain, not on the calendar. */
const TROPICS_LATITUDE = 23.5

export function foliagePhase(date: Date, latitude: number): FoliagePhase {
  if (Math.abs(latitude) < TROPICS_LATITUDE) return 'growing'
  let month = date.getMonth() + 1
  if (latitude < 0) month = ((month + 5) % 12) + 1
  if (AUTUMN_MONTHS.has(month)) return 'autumn'
  if (BARE_MONTHS.has(month)) return 'bare'
  return 'growing'
}

export const FOLIAGE_PHASE_LABEL: Record<FoliagePhase, string> = {
  growing: 'Leaf shade by species · growing season',
  autumn: 'Leaf shade by species · autumn colour',
  bare: 'Leaf shade by species · winter, deciduous trees shown by bark',
}

export const FOLIAGE_LEGEND_NOTE = 'No recorded shade yet'

/** A deciduous tree with no bark shade recorded, in winter: bare branches. */
export const BARE_BRANCH_COLOR = '#8a7f73'

/**
 * The species shade table for a phase: (species, shades), only species with
 * at least one shade. `is_evergreen` decides whether autumn and winter apply;
 * a species whose evergreen-ness is unknown takes its autumn shade when it
 * has one, and is never bared.
 */
export function speciesShadeSql(phase: FoliagePhase): string {
  const shades = phase === 'autumn'
    ? `CASE WHEN se.is_evergreen OR len(COALESCE(se.fall_foliage_colors, [])) = 0 THEN se.foliage_colors ELSE se.fall_foliage_colors END`
    : phase === 'bare'
      ? `CASE WHEN se.is_evergreen = false THEN [COALESCE(se.trunk_color, '${BARE_BRANCH_COLOR}')] ELSE se.foliage_colors END`
      : 'se.foliage_colors'
  // One row a species, so the join never multiplies a tree.
  return `SELECT species, any_value(shades) AS shades FROM (
  SELECT se.species, ${shades} AS shades FROM species_enrichment se
) WHERE len(COALESCE(shades, [])) > 0
GROUP BY species`
}

/**
 * The colours the map paints leaf shades with. The map draws one heatmap
 * layer per colour and groups its overview cells by colour, and the
 * enrichment table holds thousands of distinct hexes (one per species leaf
 * photo), so the worker snaps every shade to its nearest entry here when it
 * builds the species table. The tree card shows the exact recorded hexes.
 */
export const SHADE_PALETTE = [
  // greens, deep conifer to fresh spring leaf, and the blue-greens
  '#1f3d1c', '#2f5a2a', '#3f7034', '#5a8a3c', '#7aa54a', '#9cbf5e', '#3d6b5a', '#5f8a78',
  // autumn
  '#e8c547', '#d99a2b', '#d9661f', '#c0392b', '#8e2335', '#6d3b5c',
  // bark
  '#5e5048', '#7a5a3c', BARE_BRANCH_COLOR,
] as const

function rgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

/** The palette entry nearest a hex ("redmean" weighted RGB distance); null for something that is not a colour. */
export function snapShade(hex: string): string | null {
  const c = rgb(hex)
  if (!c) return null
  let best: string = SHADE_PALETTE[0]
  let bestDistance = Infinity
  for (const entry of SHADE_PALETTE) {
    const p = rgb(entry)!
    const rMean = (c[0] + p[0]) / 2
    const [dr, dg, db] = [c[0] - p[0], c[1] - p[1], c[2] - p[2]]
    const distance = (2 + rMean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rMean) / 256) * db * db
    if (distance < bestDistance) {
      bestDistance = distance
      best = entry
    }
  }
  return best
}
