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
 * The phase is decided here, from the calendar and the latitude, not per
 * species: the table has no leaf-out or leaf-drop dates yet. A list with
 * more than one shade is spread across the species' trees by tree id, so a
 * red maple's trees come up red and orange rather than all one hex.
 *
 * ## The season, as lookup tables
 *
 * The calendar is baked once, at module load, into two tables by whole
 * degree of latitude: the phase of each month (`foliagePhase`), and the
 * months a deciduous tree there is bare as a 12-bit mask (`leafOffMonths`;
 * bit 0 is January). The renderers never work a phase out per tree: the
 * worker gives each tree its mask (`FoliageLookup`), and a tree is bare when
 * its mask has the map date's month bit (`leafOffBit`).
 *
 * ## Which trees drop their leaves
 *
 * A binomial with a known `is_evergreen` answers for itself. A tree recorded
 * only to its genus ('Quercus'), or of a species whose habit the enrichment
 * left unknown, takes the share of its genus's identified trees that are
 * deciduous where it stands: published per 1-degree cell, with region and
 * world fallbacks, by data/raw/foliage_defaults.preql. So a generic oak in
 * Boston (every identified oak there deciduous) is bare in winter and one in
 * Naples, Florida (every one a live oak) is not. Where there is no published
 * share the enrichment's own answer stands, and an unknown one stays in leaf.
 */

export type FoliagePhase = 'growing' | 'autumn' | 'bare'

/**
 * The season by band of distance from the equator, in whole degrees (the
 * band holds from `from` up to the next band's): the northern months a
 * deciduous tree is bare and in autumn colour; the south is six months out.
 * Inside the tropics there is no autumn: deciduous trees there drop leaves on
 * rain, not on the calendar. Winters lengthen poleward: a Houston street tree
 * is bare December to February, a Boston one December to March and a
 * Helsinki one November to April.
 */
export const SEASON_BANDS: readonly { from: number; bare: readonly number[]; autumn: readonly number[] }[] = [
  { from: 0, bare: [], autumn: [] },
  { from: 24, bare: [1], autumn: [11, 12] },
  { from: 28, bare: [12, 1, 2], autumn: [10, 11] },
  { from: 34, bare: [12, 1, 2, 3], autumn: [9, 10, 11] },
  { from: 55, bare: [11, 12, 1, 2, 3, 4], autumn: [9, 10] },
]

const PHASES: readonly FoliagePhase[] = ['growing', 'autumn', 'bare']
/** Rows of the tables: whole degrees of latitude, -90 to 90. */
const LATITUDE_ROWS = 181

/** The table row of a latitude: its whole degree, clamped; NaN reads the equator. */
function latitudeRow(latitude: number): number {
  if (!Number.isFinite(latitude)) return 90
  return Math.min(LATITUDE_ROWS - 1, Math.max(0, Math.floor(latitude) + 90))
}

/**
 * The phase code (an index of PHASES) for every (row, month), and each row's
 * bare months as a mask. A row's distance from the equator is that of its
 * edge nearest it, so the tables are symmetric: [-24, -23) is in the tropics
 * as [23, 24) is.
 */
const PHASE_TABLE = new Uint8Array(LATITUDE_ROWS * 12)
const LEAF_OFF_TABLE = new Uint16Array(LATITUDE_ROWS)
for (let row = 0; row < LATITUDE_ROWS; row++) {
  const degree = row - 90
  const distance = degree >= 0 ? degree : -(degree + 1)
  const band = SEASON_BANDS.reduce((found, b) => (distance >= b.from ? b : found), SEASON_BANDS[0])
  for (let month = 1; month <= 12; month++) {
    // The northern calendar month this month is, for this hemisphere.
    const northern = degree < 0 ? ((month + 5) % 12) + 1 : month
    const phase = band.bare.includes(northern) ? 2 : band.autumn.includes(northern) ? 1 : 0
    PHASE_TABLE[row * 12 + month - 1] = phase
    if (phase === 2) LEAF_OFF_TABLE[row] |= 1 << (month - 1)
  }
}

export function foliagePhase(date: Date, latitude: number): FoliagePhase {
  return PHASES[PHASE_TABLE[latitudeRow(latitude) * 12 + date.getMonth()]]
}

/** The months a deciduous tree at this latitude is bare: bit 0 is January. */
export function leafOffMonths(latitude: number): number {
  return LEAF_OFF_TABLE[latitudeRow(latitude)]
}

/** The bit of `date`'s month in a leaf-off mask: a tree is bare when its mask has it. */
export function leafOffBit(date: Date): number {
  return 1 << date.getMonth()
}

/**
 * The 1-degree cell foliage_defaults.preql keys its genus shares on:
 * `(floor(lat) + 90) * 360 + floor(lng) + 180`, as the model computes it. The
 * two tests that pin it (data/raw/tests/test_foliage_defaults.py and
 * foliageSeason.test.ts) agree on Boston's cell.
 */
export function foliageCell(latitude: number, longitude: number): number {
  return (Math.floor(latitude) + 90) * 360 + Math.floor(longitude) + 180
}

/**
 * The species rows the lookup is built from, by the code of the world's
 * species ENUM (`enum_code`): each enriched species' habit, and the genus
 * whose published share decides it instead (`open_genus`), for a species
 * recorded to its genus alone or of an unknown habit. `enumType` is the
 * world's species type (workers/cityTables.ts CITY_ENUM_TYPES.species).
 */
export function foliageSpeciesSql(enumType: string, enrichment = 'species_enrichment'): string {
  return `SELECT CAST(enum_code(v.s) AS INTEGER) AS code, fs.deciduous, fs.open_genus
  FROM (SELECT CAST(unnest(enum_range(NULL::${enumType})) AS ${enumType}) AS s) v
  LEFT JOIN (
    SELECT species,
      any_value(is_evergreen = false) AS deciduous,
      any_value(CASE WHEN is_evergreen IS NULL OR species = genus THEN genus END) AS open_genus
    FROM ${enrichment} WHERE species IS NOT NULL GROUP BY species
  ) fs ON CAST(v.s AS VARCHAR) = fs.species`
}

/** The published genus shares as the lookup reads them: deciduous when most identified trees there are. */
export function foliageDefaultsSql(file: string): string {
  return `SELECT CAST(cell AS INTEGER) AS cell, genus, deciduous_share >= 0.5 AS deciduous
  FROM read_parquet('${file}') WHERE deciduous_share IS NOT NULL AND genus IS NOT NULL`
}

/** A tree's species as a query hands it over: its code in the world's species ENUM, -1 for none. */
export function speciesCodeSql(alias: string): string {
  return `COALESCE(CAST(enum_code(${alias}.species) AS INTEGER), -1)`
}

/** A species that keeps its leaves, or whose habit nothing answers. */
const KEEPS = -1
/** A species that drops them, wherever it stands. */
const DROPS = -2

/**
 * Whether a tree drops its leaves, and when, by its species code and where
 * it stands: built once a world (the species ENUM is the world's), read per
 * tree as typed-array and map lookups instead of a join in every query. A
 * crown read and a chunk of dots each ask it once per tree.
 */
export class FoliageLookup {
  /** By species code: KEEPS, DROPS, or the index of the genus whose share decides it. */
  private readonly habit: Int32Array
  /** By species code: the enrichment's own answer for an open species, where its genus has no share here. */
  private readonly fallback: Uint8Array
  /** Deciduous by `cell * genera + genus index`, for the cells with a published share. */
  private readonly shares = new Map<number, boolean>()
  private readonly genera: number

  constructor(
    species: Iterable<{ code: number; deciduous: boolean | null; open_genus: string | null }>,
    defaults: Iterable<{ cell: number; genus: string; deciduous: boolean }>,
  ) {
    const rows = [...species]
    const size = rows.reduce((max, r) => Math.max(max, r.code + 1), 0)
    this.habit = new Int32Array(size).fill(KEEPS)
    this.fallback = new Uint8Array(size)
    const genusIndex = new Map<string, number>()
    for (const r of rows) {
      if (r.code < 0) continue
      if (r.open_genus) {
        let g = genusIndex.get(r.open_genus)
        if (g === undefined) genusIndex.set(r.open_genus, g = genusIndex.size)
        this.habit[r.code] = g
        this.fallback[r.code] = r.deciduous ? 1 : 0
      } else if (r.deciduous) {
        this.habit[r.code] = DROPS
      }
    }
    this.genera = Math.max(1, genusIndex.size)
    for (const d of defaults) {
      const g = genusIndex.get(d.genus)
      if (g !== undefined) this.shares.set(d.cell * this.genera + g, !!d.deciduous)
    }
  }

  /** An empty lookup: every tree keeps its leaves. */
  static none(): FoliageLookup {
    return new FoliageLookup([], [])
  }

  /** Whether the tree's species may be bare somewhere: a tree that never is needs no position. */
  mayDrop(code: number): boolean {
    return code >= 0 && code < this.habit.length && this.habit[code] !== KEEPS
  }

  /** The months the tree is bare (bit 0 January); 0 for one that keeps its leaves. */
  leafOff(code: number, latitude: number, longitude: number): number {
    if (!this.mayDrop(code)) return 0
    const h = this.habit[code]
    const drops = h === DROPS
      || (this.shares.get(foliageCell(latitude, longitude) * this.genera + h) ?? this.fallback[code] === 1)
    return drops ? leafOffMonths(latitude) : 0
  }
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
