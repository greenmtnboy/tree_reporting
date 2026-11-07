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
