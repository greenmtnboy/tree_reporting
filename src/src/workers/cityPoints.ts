// The desktop map's resident point set: every tree the current query draws,
// as typed arrays the main thread uploads to the GPU once (TreeDotsLayer).
// A colour change re-reads only the one-byte colour per tree.

/** Index 0 of every palette: a colour the palette does not list. */
export const POINT_FALLBACK_COLOR = '#66BB6A'
/** Palette entries a one-byte index can address after the fallback. */
export const MAX_POINT_PALETTE = 255

/** Rows for one query and filter; positions are 3857 metres from the origin. */
export interface CityPoints {
  /** The row set's identity: a colour change keeps it, a query or filter change does not. */
  rowsKey: string
  colorKey: string
  count: number
  originX: number
  originY: number
  x: Float32Array
  y: Float32Array
  /** Trunk diameter, whole inches, 1 to 255 (3 where unknown, as the tiles drew it). */
  dbh: Uint8Array
  /** Index into `palette`. */
  color: Uint8Array
  palette: string[]
  /** trees_fast rowid, for the tree card lookup and to scatter a colour refresh. */
  rowid: Uint32Array
}

/** A colour refresh for the rows of `rowsKey`, in any order. */
export interface CityPointColors {
  rowsKey: string
  colorKey: string
  color: Uint8Array
  palette: string[]
  rowid: Uint32Array
}

const sqlString = (value: string) => `'${value.replace(/'/g, "''")}'`

/** Fallback first, then the scheme's colours, at most one byte's worth. */
export function pointPalette(colors: readonly string[]): string[] {
  const unique = [...new Set(colors.filter((c) => c && c !== POINT_FALLBACK_COLOR))]
  return [POINT_FALLBACK_COLOR, ...unique.slice(0, MAX_POINT_PALETTE)]
}

/** A colour expression's palette index; anything unlisted is 0, the fallback. */
export function paletteIndexSql(colorExpr: string, palette: readonly string[]): string {
  const listed = palette.slice(1)
  if (!listed.length) return 'CAST(0 AS UTINYINT)'
  return `CAST(COALESCE(list_position([${listed.map(sqlString).join(', ')}], ${colorExpr}), 0) AS UTINYINT)`
}

export interface PointRowSource {
  prelude: string
  from: string
  where: string
  dbh: string
}

/** Every drawn tree's position, size, colour and rowid. */
export function cityPointsSql(src: PointRowSource, colorExpr: string, palette: readonly string[], originX: number, originY: number): string {
  return `${src.prelude} __unused AS (SELECT 1)
SELECT
  CAST(tf.rowid AS UINTEGER) AS rowid,
  CAST(tf.x_3857 - ${originX} AS FLOAT) AS x,
  CAST(tf.y_3857 - ${originY} AS FLOAT) AS y,
  CAST(LEAST(255, GREATEST(1, round(${src.dbh}))) AS UTINYINT) AS dbh,
  ${paletteIndexSql(colorExpr, palette)} AS color
FROM ${src.from}
WHERE ${src.where}TRUE`
}

/** The same rows' colour alone. */
export function cityPointColorsSql(src: PointRowSource, colorExpr: string, palette: readonly string[]): string {
  return `${src.prelude} __unused AS (SELECT 1)
SELECT
  CAST(tf.rowid AS UINTEGER) AS rowid,
  ${paletteIndexSql(colorExpr, palette)} AS color
FROM ${src.from}
WHERE ${src.where}TRUE`
}
