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

const hex2 = (v: number) => v.toString(16).padStart(2, '0')

function rgb(hex: string): [number, number, number] | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

type Rgb = [number, number, number]

/** "Redmean" weighted RGB distance, squared: cheap, and closer to the eye than plain RGB. */
function shadeDistance(c: Rgb, p: Rgb): number {
  const rMean = (c[0] + p[0]) / 2
  const dr = c[0] - p[0], dg = c[1] - p[1], db = c[2] - p[2]
  return (2 + rMean / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rMean) / 256) * db * db
}

/** A shade whose green channel leads: a leaf, whatever its grey or olive cast. */
const greenLed = (c: Rgb) => c[1] >= c[0] && c[1] >= c[2]

/**
 * How many colours the overview tiles (z11 to z14, grid cells under a
 * heatmap) paint leaf shades with, on a phone or a desktop without WebGL2.
 * Each colour is a heatmap and a circle layer, so the count is their cost.
 * Over the published table's 3,260 growing-season shades the mean redmean
 * error is 42 at eight fitted colours, against 44 for the 17 fixed colours
 * the tiles painted before. The tree tiles (z15 up) carry their colour on
 * each feature and take the fine palette (fineShadeMap).
 */
export const OVERVIEW_SHADE_COUNT = 8

/**
 * Each recorded shade's paint colour from `count` colours fitted to the
 * shades themselves (k-means under the redmean distance, from the farthest
 * points, so the same shades always give the same colours). A green-led shade
 * takes the nearest green-led colour: the grey- and olive-greens of conifers
 * and glaucous foliage (Pinus parviflora's #778e68) can sit nearer a bark
 * colour, and an evergreen painted bark reads as a bare tree. Something not a
 * colour is left out.
 */
export function fittedShadeMap(hexes: Iterable<string>, count: number, iterations = 12): Map<string, string> {
  const shades = new Map<string, Rgb>()
  for (const hex of hexes) {
    const c = rgb(hex)
    if (c) shades.set(hex, c)
  }
  const points = [...shades.values()]
  if (!points.length || count < 1) return new Map()
  const mean: Rgb = [0, 1, 2].map((i) => points.reduce((s, c) => s + c[i], 0) / points.length) as Rgb
  let first = points[0]
  for (const c of points) if (shadeDistance(c, mean) < shadeDistance(first, mean)) first = c
  const centres: Rgb[] = [[...first]]
  const nearestSoFar = points.map((c) => shadeDistance(c, first))
  while (centres.length < Math.min(count, points.length)) {
    let far = 0
    for (let i = 1; i < points.length; i++) if (nearestSoFar[i] > nearestSoFar[far]) far = i
    if (nearestSoFar[far] === 0) break
    centres.push([...points[far]])
    for (let i = 0; i < points.length; i++) nearestSoFar[i] = Math.min(nearestSoFar[i], shadeDistance(points[i], points[far]))
  }
  const nearest = (c: Rgb, among: readonly number[]) => {
    let best = among[0], bestDistance = Infinity
    for (const j of among) {
      const d = shadeDistance(c, centres[j])
      if (d < bestDistance) { bestDistance = d; best = j }
    }
    return best
  }
  const all = centres.map((_, j) => j)
  for (let it = 0; it < iterations; it++) {
    const sums = centres.map(() => [0, 0, 0, 0])
    for (const c of points) {
      const s = sums[nearest(c, all)]
      s[0] += c[0]; s[1] += c[1]; s[2] += c[2]; s[3]++
    }
    sums.forEach((s, j) => { if (s[3]) centres[j] = [s[0] / s[3], s[1] / s[3], s[2] / s[3]] })
  }
  const paints = centres.map((c) => '#' + c.map((v) => hex2(Math.round(v))).join(''))
  const greens = all.filter((j) => greenLed(centres[j]))
  const out = new Map<string, string>()
  for (const [hex, c] of shades) out.set(hex, paints[nearest(c, greenLed(c) && greens.length ? greens : all)])
  return out
}

/**
 * Distinct shades the desktop's dots and the crowns may paint. The dots
 * address a palette by one byte (workers/cityPoints.ts MAX_POINT_PALETTE,
 * 255 after the fallback); the rest is left for a chat override's colours
 * and the no-shade grey.
 */
export const FINE_SHADE_BUDGET = 200

/**
 * Each recorded shade's paint colour where a palette of `budget` entries is
 * all the renderer needs: the shades binned on an even RGB grid, each bin
 * painted the mean of the shades in it, the grid's step the finest that
 * leaves no more than `budget` bins. Over the published table (3,260
 * growing-season shades) that is a step of 26 and a mean redmean error of 14.
 * Something not a colour is left out.
 */
export function fineShadeMap(hexes: Iterable<string>, budget = FINE_SHADE_BUDGET): Map<string, string> {
  const colors = new Map<string, [number, number, number]>()
  for (const hex of hexes) {
    const c = rgb(hex)
    if (c) colors.set(hex, c)
  }
  for (let step = 1; step <= 256; step += step < 8 ? 1 : 2) {
    const bins = new Map<string, { sum: [number, number, number]; members: string[] }>()
    for (const [hex, c] of colors) {
      const key = `${Math.floor(c[0] / step)},${Math.floor(c[1] / step)},${Math.floor(c[2] / step)}`
      let bin = bins.get(key)
      if (!bin) bins.set(key, bin = { sum: [0, 0, 0], members: [] })
      bin.sum[0] += c[0]; bin.sum[1] += c[1]; bin.sum[2] += c[2]
      bin.members.push(hex)
    }
    if (bins.size > budget) continue
    const out = new Map<string, string>()
    for (const { sum, members } of bins.values()) {
      const paint = '#' + sum.map((s) => hex2(Math.round(s / members.length))).join('')
      for (const hex of members) out.set(hex, paint)
    }
    return out
  }
  return new Map()
}
