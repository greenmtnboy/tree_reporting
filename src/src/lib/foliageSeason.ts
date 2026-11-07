/**
 * The shade a species' tree is painted for the time of year.
 *
 * The enrichment table carries three CSS hex columns per species
 * (data/raw/tree_enrichment.preql): `foliage_colors`, the growing-season leaf;
 * `fall_foliage_colors`, the autumn leaf, empty for an evergreen; and
 * `trunk_color`, the bark. The map's "Leaf shade" colour mode reads them
 * through the colour-override path the DBH mode already uses: a query over
 * the `trees` view returning (tree_id, override_color), and a tree the query
 * gives no colour keeps its tree-form colour. Coverage is sparse to start,
 * so that fallback is most of the map for now.
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

export const FOLIAGE_LEGEND_NOTE = 'Species with no recorded shade keep their tree-form colour.'

/** A deciduous tree with no bark shade recorded, in winter: bare branches. */
export const BARE_BRANCH_COLOR = '#8a7f73'

/** One shade of a list, chosen by the tree's id so a two-shade species renders mixed. */
function pickShade(listExpr: string): string {
  return `list_extract(${listExpr}, 1 + CAST(hash(t.tree_id) % len(${listExpr}) AS INTEGER))`
}

/**
 * The override query for a phase. `is_evergreen` decides whether autumn and
 * winter apply; a species whose evergreen-ness is unknown is treated as
 * deciduous in autumn only when it has an autumn shade, and is never bared.
 */
export function foliageColorSql(phase: FoliagePhase): string {
  const growing = pickShade('se.foliage_colors')
  const shade = phase === 'autumn'
    ? `CASE WHEN se.is_evergreen THEN ${growing} ELSE COALESCE(${pickShade('se.fall_foliage_colors')}, ${growing}) END`
    : phase === 'bare'
      ? `CASE WHEN se.is_evergreen = false THEN COALESCE(se.trunk_color, '${BARE_BRANCH_COLOR}') ELSE ${growing} END`
      : growing
  return `SELECT t.tree_id, ${shade} AS override_color
FROM trees t
JOIN species_enrichment se ON t.species = se.species`
}
