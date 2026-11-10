import type { CustomLayerInterface, Map as MapLibreMap } from 'maplibre-gl'
import {
  CHUNK_SIZE_M, chunkOrigin, chunksInBox,
  type ChunkGrid, type PointChunkBatch, type PointChunkColors, type PointChunkDirectory, type PointKeys,
} from '../workers/cityPoints'
import {
  CIRCLE_MAX_ZOOM, CIRCLE_PICK_MIN_ZOOM,
  CIRCLE_FAR_OPACITY, circleOpacity, visibleRanges, circleRadiusRange, dotRadius, pixelsPerMetre, CROWN_BYTE_PER_M,
  hexRgb, lngLatFrom3857, lngLatTo3857, metresPerPixel,
  pointsNear, relativeMatrix,
} from '../lib/treeDots'
import { DEFAULT_CROWN_WIDTH, MAX_CROWN_WIDTH, REACH_FADE } from '../lib/treeCrowns'

/**
 * The desktop map's trees below the sprites: trunk-sized circles from z6
 * until the sprites take over, drawn from typed arrays on the GPU.
 * It replaces the vector-tile source and its per-colour heatmap and circle
 * layers on the desktop (docs/MAP_COLOR_PERF_AUDIT.md):
 *
 * - The trees arrive in chunks of one global grid (workers/cityPoints.ts:
 *   Web Mercator tiles at z12, each one block file of the published world),
 *   never as a city: the chunks in view first, then the nearest others in
 *   the background out to a radius and up to a point budget, the farthest
 *   dropped past it. Which chunks exist is the world's manifest, so the
 *   camera can leave one inventory and arrive in another and the layer only
 *   ever asks for the ground it is looking at. Each chunk carries its own
 *   origin, buffers and pick grid.
 * - Below LOAD_MIN_ZOOM a view spans several metros, so nothing new is
 *   loaded there: what is held stays drawn, and the city markers
 *   (useGlobeCityMarkers.ts) say where the rest is.
 * - Panning and zooming within what is loaded never waits: a frame draws
 *   only the ranges of the chunks in view.
 * - A colour change re-reads one byte per tree, chunks in view first, and
 *   swaps it in; the old colours stay drawn until then. A query or filter
 *   change keeps the old trees drawn until the new rows in view arrive.
 *
 * Needs WebGL2; `TreeDotsLayer.supported` says so before the map adds it,
 * and the vector tiles remain otherwise.
 */

type GL2 = WebGL2RenderingContext

export interface TreeDotsSource {
  getPointKeys: () => Promise<PointKeys | null>
  getPointChunkDirectory: () => Promise<PointChunkDirectory | null>
  getPointChunks: (rowsKey: string, keys: number[]) => Promise<PointChunkBatch | null>
  getPointChunkColors: (rowsKey: string, keys: number[]) => Promise<PointChunkColors | null>
}

export interface TreeDotsPick {
  /** The tree's chunk and its row in that chunk's block file: what names it to the worker. */
  key: number
  rowid: number
  lng: number
  lat: number
}

/** Trees held at most; past it the chunks farthest from the camera are dropped. */
const MAX_RESIDENT_POINTS = 6_000_000
/**
 * Trees one background read may bring in. DuckDB answers one statement at
 * a time, so this bounds how long a pan's own read can wait behind one.
 */
// Small enough that a sprite query arriving mid-batch is not held long: every
// query in the worker shares one DuckDB connection, which runs them in turn.
const PREFETCH_BATCH_POINTS = 100_000
/** How long a deferred prefetch waits before asking again. */
const PREFETCH_RETRY_MS = 300
/**
 * The longest the prefetch defers in a row. Sprite queries are due almost
 * continuously while the camera moves; deferring every time starved the
 * prefetch, and chunks entering the view then had no trees of either kind.
 */
const PREFETCH_MAX_DEFER_MS = 1000
/** The view's box is padded by this share of its size for what counts as in view. */
const VIEW_PAD = 0.25
/**
 * Below this zoom no chunk is loaded. A 1280 px view at z9 is about 200 km
 * across at 45 degrees: one metro, or two small neighbours. A zoom lower
 * and it is the north-east corridor, several million trees for dots a
 * fraction of a pixel wide.
 */
export const LOAD_MIN_ZOOM = 9
/**
 * Trees one read of the view may bring in. A metro seen whole is a couple of
 * hundred chunks; in batches the nearest draw while the rest download.
 */
const VIEW_BATCH_POINTS = 300_000
/** Chunks one read may span, whatever their size: each is a file to fetch. */
const VIEW_BATCH_CHUNKS = 24
/**
 * How far past the view the background reads reach, in metres on the
 * ground: the rest of the metro the camera is in, not the next one.
 */
const PREFETCH_RADIUS_M = 45_000

function compile(gl: GL2, vertex: string, fragment: string, name: string): WebGLProgram {
  const program = gl.createProgram()!
  for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]] as const) {
    const shader = gl.createShader(type)!
    gl.shaderSource(shader, source)
    gl.compileShader(shader)
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const error = gl.getShaderInfoLog(shader)
      gl.deleteShader(shader); gl.deleteProgram(program)
      throw new Error(`${name} shader: ${error}`)
    }
    gl.attachShader(program, shader)
    gl.deleteShader(shader)
  }
  gl.linkProgram(program)
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const error = gl.getProgramInfoLog(program)
    gl.deleteProgram(program)
    throw new Error(`${name} link: ${error}`)
  }
  return program
}

/** The widest dot's radius at the view's centre, CSS pixels: what drawing and picking pad by. Capped, as the widest crowns near the camera sit under their sprites. */
function maxDotRadius(range: readonly [number, number], pxPerMetre: number): number {
  return Math.max(range[1], Math.min(256, (MAX_CROWN_WIDTH / 2) * pxPerMetre))
}

function uniforms(gl: GL2, program: WebGLProgram, names: string[]): Record<string, WebGLUniformLocation | null> {
  return Object.fromEntries(names.map((n) => [n, gl.getUniformLocation(program, n)]))
}

const CIRCLE_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec2 a_pos;
// Palette index, dbh (inches), crown width (CROWN_BYTE_PER_M; 0 unknown).
layout(location = 1) in vec4 a_attr;
uniform mat4 u_matrix;
uniform vec2 u_center;
uniform vec2 u_radius;
uniform float u_dpr;
uniform float u_camdist;
// CSS pixels per real metre at the view's centre (lib/treeDots pixelsPerMetre).
uniform float u_px_per_m;
uniform sampler2D u_palette;
uniform float u_opacity;
// Metres the sprites reach; < 0 below the sprite zooms.
uniform float u_reach;
// The sprite batch's fetch footprint in chunk coordinates: a convex polygon, counter-clockwise,
// its last corner repeated when it has three. All zero (nothing inside) when no batch is installed.
uniform vec2 u_poly[4];
uniform float u_far_opacity;
// Camera height in metres, and real metres per EPSG:3857 metre at the camera.
uniform float u_altitude;
uniform float u_scale;
// The sprite batch's query camera, on the ground in chunk coordinates (off the centre when pitched).
uniform vec2 u_eye;
out vec3 v_color;
out float v_radius;
out float v_opacity;
void main() {
  gl_Position = u_matrix * vec4(a_pos - u_center, 0.0, 1.0);
  v_opacity = u_opacity;
  if (u_reach >= 0.0) {
    // Fill in past the sprites: a dot shows as much as the sprite over it has faded.
    float dist = length(vec3((a_pos - u_eye) * u_scale, u_altitude));
    float inBox = 1.0;
    for (int i = 0; i < 4; i++) {
      vec2 a = u_poly[i], b = u_poly[(i + 1) % 4], e = b - a, d = a_pos - a;
      // A repeated corner is a zero-length edge, which passes.
      inBox *= step(0.0, e.x * d.y - e.y * d.x);
    }
    inBox *= step(1e-6, length(u_poly[1] - u_poly[0]) + length(u_poly[2] - u_poly[1]));
    float sprite = inBox * (1.0 - smoothstep(u_reach * ${REACH_FADE}, u_reach, dist));
    v_opacity = max(u_opacity, u_far_opacity * (1.0 - sprite));
  }
  float s = clamp(sqrt(a_attr.y) / ${Math.sqrt(42).toFixed(6)}, 0.0, 1.0);
  // lib/treeDots dotRadius: the crown's disc at metre scale, the trunk circle its floor.
  float crown = a_attr.z > 0.0 ? a_attr.z / ${CROWN_BYTE_PER_M.toFixed(1)} : ${DEFAULT_CROWN_WIDTH.toFixed(1)};
  float radius = max(mix(u_radius.x, u_radius.y, s), crown * 0.5 * u_px_per_m) * u_dpr * (gl_Position.w > 0.0 ? u_camdist / gl_Position.w : 0.0);
  v_radius = radius;
  gl_PointSize = 2.0 * radius + 2.0;
  v_color = texelFetch(u_palette, ivec2(int(a_attr.x), 0), 0).rgb;
}`

const CIRCLE_FS = `#version 300 es
precision highp float;
in vec3 v_color;
in float v_radius;
in float v_opacity;
out vec4 o;
void main() {
  float d = length(gl_PointCoord - 0.5) * (2.0 * v_radius + 2.0);
  float a = clamp(v_radius - d + 0.5, 0.0, 1.0) * v_opacity;
  if (a <= 0.0) discard;
  o = vec4(v_color * a, a);
}`


interface ChunkGpu {
  pos: WebGLBuffer
  attr: WebGLBuffer
  pointVao: WebGLVertexArrayObject
}

/** One chunk of trees, positions in metres from its south-west corner and stored in draw-grid order. */
interface Chunk {
  key: number
  originX: number
  originY: number
  count: number
  x: Float32Array
  y: Float32Array
  dbh: Uint8Array
  crown: Uint8Array
  color: Uint8Array
  rowid: Uint32Array
  /** A row's index here by its rowid; -1 for a row the current filter leaves out. */
  indexByRowid: Int32Array
  palette: string[]
  colorKey: string
  /** 512 m cells over the points: the draw ranges and the pick index. */
  grid: ChunkGrid
  gpu?: ChunkGpu
  uploadPoints: boolean
  uploadColors: boolean
}

type Box = [number, number, number, number]

function intersects(a: Box, b: Box): boolean {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1]
}

export class TreeDotsLayer implements CustomLayerInterface {
  readonly id = 'trees-dots'
  readonly type = 'custom' as const
  readonly renderingMode = '2d' as const

  private map?: MapLibreMap
  private gl?: GL2
  private circleProgram?: WebGLProgram
  private circleU: Record<string, WebGLUniformLocation | null> = {}
  private palettes = new Map<string, WebGLTexture>()
  private dropped: ChunkGpu[] = []

  // --- What is drawn ---
  private chunks = new Map<number, Chunk>()
  /** The installed rows: their identity, the chunks that may hold any, and the world's tree count. */
  private rowsKey?: string
  private directory = new Map<number, number>()
  private total = 0
  /** False while a query or filter narrows the rows: the directory's counts are then upper bounds. */
  private exact = true
  private installed = false
  private settledView = false
  /** Chunks read and found to hold none of the current rows. */
  private empty = new Set<number>()
  /** The colouring every chunk should have. */
  private colorKey?: string

  // --- Loading ---
  private generation = 0
  private checkKeys = false
  private wantRows?: string
  private pendingDirectory?: PointChunkDirectory
  private busy = false
  private wake = false
  private waiters: (() => void)[] = []
  private moveTimer?: ReturnType<typeof setTimeout>
  private prefetchTimer?: ReturnType<typeof setTimeout>
  private deferredSince?: number

  /** Last frame's camera, for picking exactly what was drawn. */
  private frame?: { matrix: Float64Array; width: number; height: number; camdist: number; radius: [number, number]; pxPerMetre: number }

  constructor(
    private source: TreeDotsSource,
    private onLoaded?: (count: number) => void,
    /** Where the sprites stand in for the dots (see TreeCrownLayer.spriteCoverage); elsewhere the dots stay drawn at any zoom. */
    private spriteCoverage: () => {
      /** EPSG:3857 metres, counter-clockwise (lib/viewFootprint). */
      polygon: [number, number][] | null
      reach: number
      eye: { lng: number; lat: number; altitude: number } | null
    } | null = () => null,
    /** True while a sprite query is due or running; the background prefetch waits behind it. */
    private spritesBusy: () => boolean = () => false,
  ) {}

  /** WebGL2: vertex array objects and texelFetch for the palette. */
  static supported(map: MapLibreMap): boolean {
    const gl = (map as unknown as { painter?: { context?: { gl?: unknown } } }).painter?.context?.gl
    return typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext
  }

  /**
   * Trees in the current rows: the world's, loaded or not, or under a query
   * or filter the matching trees held, since no one has counted the rest.
   * 0 until the rows are known.
   */
  get count(): number { return this.exact ? this.total : this.residentCount }
  /** Trees held in memory. */
  get residentCount(): number { let n = 0; for (const c of this.chunks.values()) n += c.count; return n }
  get chunkCount(): number { return this.chunks.size }
  /** True while every chunk in view holds the current rows and colours. */
  get viewSettled(): boolean { return this.settledView }
  get loaded(): boolean { return this.installed }

  onAdd(map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map = map
    this.gl = gl as GL2
    this.initialize()
    map.on('webglcontextrestored', this.restore)
    map.on('moveend', this.schedule)
    map.on('move', this.onMove)
    for (const chunk of this.chunks.values()) this.markUpload(chunk)
    this.schedule()
  }

  onRemove(_map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map?.off('webglcontextrestored', this.restore)
    this.map?.off('moveend', this.schedule)
    this.map?.off('move', this.onMove)
    clearTimeout(this.moveTimer)
    clearTimeout(this.prefetchTimer)
    const g = gl as GL2
    for (const chunk of this.chunks.values()) if (chunk.gpu) { this.dropped.push(chunk.gpu); chunk.gpu = undefined }
    this.release(g)
    this.map = undefined
    this.gl = undefined
  }

  private onMove = () => {
    if (this.moveTimer) return
    this.moveTimer = setTimeout(() => { this.moveTimer = undefined; this.schedule() }, 150)
  }

  private markUpload(chunk: Chunk) {
    chunk.gpu = undefined
    chunk.uploadPoints = chunk.uploadColors = true
  }

  private restore = () => {
    if (!this.gl) return
    this.palettes.clear()
    this.initialize()
    for (const chunk of this.chunks.values()) this.markUpload(chunk)
    this.map?.triggerRepaint()
  }

  private release(gl: GL2) {
    this.freeDropped(gl)
    if (this.circleProgram) gl.deleteProgram(this.circleProgram)
    for (const t of this.palettes.values()) gl.deleteTexture(t)
    this.palettes.clear()
  }

  private freeDropped(gl: GL2) {
    for (const gpu of this.dropped) {
      for (const b of [gpu.pos, gpu.attr]) gl.deleteBuffer(b)
      gl.deleteVertexArray(gpu.pointVao)
    }
    this.dropped = []
  }

  private initialize() {
    const gl = this.gl!
    this.circleProgram = compile(gl, CIRCLE_VS, CIRCLE_FS, 'Circle')
    this.circleU = uniforms(gl, this.circleProgram, ['u_matrix', 'u_center', 'u_radius', 'u_dpr', 'u_camdist', 'u_px_per_m', 'u_palette', 'u_opacity', 'u_reach', 'u_far_opacity', 'u_altitude', 'u_scale', 'u_eye', 'u_poly'])
  }

  private createGpu(gl: GL2): ChunkGpu {
    const gpu: ChunkGpu = {
      pos: gl.createBuffer()!, attr: gl.createBuffer()!, pointVao: gl.createVertexArray()!,
    }
    gl.bindVertexArray(gpu.pointVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.pos)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.attr)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 4, gl.UNSIGNED_BYTE, false, 4, 0)
    gl.bindVertexArray(null)
    gl.bindBuffer(gl.ARRAY_BUFFER, null)
    return gpu
  }

  private paletteTexture(gl: GL2, palette: string[]): WebGLTexture {
    const key = palette.join(',')
    const cached = this.palettes.get(key)
    if (cached) return cached
    // A recolour leaves the old palette unused; a few are kept for the chunks still on it.
    if (this.palettes.size >= 4) {
      const used = new Set([...this.chunks.values()].map((c) => c.palette.join(',')))
      for (const [k, t] of this.palettes) if (!used.has(k)) { gl.deleteTexture(t); this.palettes.delete(k) }
    }
    const texture = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, texture)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    const pal = new Uint8Array(256 * 4)
    palette.forEach((hex, i) => { const [r, g, b] = hexRgb(hex); pal.set([r, g, b, 255], i * 4) })
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4)
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, pal)
    gl.bindTexture(gl.TEXTURE_2D, null)
    this.palettes.set(key, texture)
    return texture
  }

  // --- Loading ---

  /** Drop everything drawn at once: nothing of the old rows may stay on screen. */
  clear() {
    this.generation++
    this.dropAll()
    this.rowsKey = undefined
    this.installed = false
    this.directory = new Map()
    this.empty.clear()
    this.total = 0
    this.frame = undefined
    this.map?.triggerRepaint()
  }

  /**
   * Bring what is drawn up to the worker's query and colours. Resolves once
   * the chunks in view are current; the rest keep loading behind it.
   */
  sync(): Promise<void> {
    this.checkKeys = true
    const settled = new Promise<void>((resolve) => this.waiters.push(resolve))
    this.schedule()
    return settled
  }

  private settle() {
    const waiters = this.waiters
    this.waiters = []
    // A sync that settled is a view drawn from the current rows: the map's cue that its trees are up.
    if (waiters.length && this.installed) this.onLoaded?.(this.count)
    for (const resolve of waiters) resolve()
  }

  private schedule = () => {
    this.wake = true
    if (!this.busy) void this.pump()
  }

  private async pump() {
    this.busy = true
    try {
      while (this.wake) {
        this.wake = false
        if (await this.step()) this.wake = true
      }
    } catch (error) {
      // What is drawn stays; the next query, colour change or move retries.
      console.warn('[TreeDots] load failed', error)
    } finally {
      this.busy = false
      if (!this.checkKeys && !this.wantRows) this.settle()
    }
  }

  /** One read, the most urgent first. True when it did one and another may be due. */
  private async step(): Promise<boolean> {
    const generation = this.generation
    const stale = () => generation !== this.generation
    this.settledView = false
    if (this.checkKeys) {
      this.checkKeys = false
      const keys = await this.source.getPointKeys()
      if (stale()) return true
      if (!keys) return false
      this.colorKey = keys.colorKey
      if (keys.rowsKey !== this.rowsKey) this.wantRows = keys.rowsKey
      return true
    }
    if (this.wantRows) {
      if (this.pendingDirectory?.rowsKey !== this.wantRows) {
        const directory = await this.source.getPointChunkDirectory()
        if (stale()) return true
        if (!directory) { this.wantRows = undefined; return false }
        this.wantRows = directory.rowsKey
        this.colorKey = directory.colorKey
        this.pendingDirectory = directory
        return true
      }
      const directory = this.pendingDirectory
      const known = new Map(directory.keys.map((k, i) => [k, directory.counts[i]]))
      // The nearest of the view with the swap; the rest of it follows in the reads below.
      const visible = this.batchOf(this.inView(known), known)
      const batch = visible.length ? await this.source.getPointChunks(directory.rowsKey, visible) : null
      if (stale()) return true
      if (visible.length && !batch) { this.checkKeys = true; return true }
      this.install(directory, known, batch)
      return true
    }
    if (!this.installed || !this.rowsKey || !this.map) return false
    const visible = this.inView(this.directory)
    const missing = this.batchOf(visible.filter((k) => !this.chunks.has(k) && !this.empty.has(k)), this.directory)
    if (missing.length) {
      this.evict(missing.reduce((n, k) => n + (this.directory.get(k) ?? 0), 0), new Set(visible))
      return this.load(missing)
    }
    const inView = new Set(visible)
    const offColor = [...this.chunks.values()].filter((c) => c.colorKey !== this.colorKey)
    const offInView = offColor.filter((c) => inView.has(c.key))
    if (offColor.length) return this.recolor((offInView.length ? offInView : offColor).map((c) => c.key))
    // The view is current: what a sync waits on. A frame says so to whoever reads the map on idle.
    if (!this.settledView) this.map.triggerRepaint()
    this.settledView = true
    this.settle()
    // The rest of the city is background work: it yields to a moving camera and to sprite queries.
    const now = performance.now()
    if ((this.map.isMoving() || this.spritesBusy()) && now - (this.deferredSince ??= now) < PREFETCH_MAX_DEFER_MS) {
      clearTimeout(this.prefetchTimer)
      this.prefetchTimer = setTimeout(this.schedule, PREFETCH_RETRY_MS)
      return false
    }
    this.deferredSince = undefined
    if (this.map.getZoom() < LOAD_MIN_ZOOM) return false
    const resident = this.residentCount
    const rest: number[] = []
    let batch = 0
    for (const key of this.byDistance(this.around(PREFETCH_RADIUS_M).filter((k) => !this.chunks.has(k) && !this.empty.has(k)))) {
      const n = this.directory.get(key) ?? 0
      if (resident + batch + n > MAX_RESIDENT_POINTS) break
      if (batch && batch + n > PREFETCH_BATCH_POINTS) break
      rest.push(key)
      batch += n
    }
    return rest.length ? this.load(rest) : false
  }

  /**
   * The keys of `known` chunks in the padded view, nearest the centre first;
   * none below LOAD_MIN_ZOOM, where a view is too much of the world to load.
   */
  private inView(known: Map<number, number>): number[] {
    if (!this.map || this.map.getZoom() < LOAD_MIN_ZOOM) return []
    const [x0, y0, x1, y1] = this.viewBox3857(VIEW_PAD)
    return this.inBox(x0, y0, x1, y1, known)
  }

  /** The keys of `known` chunks a box touches, nearest its centre first. */
  private inBox(x0: number, y0: number, x1: number, y1: number, known: Map<number, number>): number[] {
    // A box wider than the directory is long is walked from the directory's side.
    const span = Math.max(1, Math.ceil((x1 - x0) / CHUNK_SIZE_M)) * Math.max(1, Math.ceil((y1 - y0) / CHUNK_SIZE_M))
    if (span <= known.size * 4) return chunksInBox(x0, y0, x1, y1).filter((k) => known.has(k))
    const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2
    const hits: { k: number; d: number }[] = []
    for (const k of known.keys()) {
      const [ox, oy] = chunkOrigin(k)
      if (ox > x1 || ox + CHUNK_SIZE_M < x0 || oy > y1 || oy + CHUNK_SIZE_M < y0) continue
      hits.push({ k, d: (ox + CHUNK_SIZE_M / 2 - cx) ** 2 + (oy + CHUNK_SIZE_M / 2 - cy) ** 2 })
    }
    return hits.sort((a, b) => a.d - b.d).map((h) => h.k)
  }

  /** The directory's chunks within `metres` on the ground of the view's box. */
  private around(metres: number): number[] {
    const [x0, y0, x1, y1] = this.viewBox3857()
    const reach = metres / Math.max(0.05, Math.cos((this.map!.getCenter().lat * Math.PI) / 180))
    return this.inBox(x0 - reach, y0 - reach, x1 + reach, y1 + reach, this.directory)
  }

  /** The head of `keys` one read brings in: a bounded number of trees and of files. */
  private batchOf(keys: number[], counts: Map<number, number>): number[] {
    const batch: number[] = []
    let points = 0
    for (const key of keys) {
      const n = counts.get(key) ?? 0
      if (batch.length && (points + n > VIEW_BATCH_POINTS || batch.length >= VIEW_BATCH_CHUNKS)) break
      batch.push(key)
      points += n
    }
    return batch
  }

  private byDistance(keys: number[]): number[] {
    const c = this.map!.getCenter()
    const [cx, cy] = lngLatTo3857(c.lng, c.lat)
    const d = (k: number) => { const [ox, oy] = chunkOrigin(k); return (ox + CHUNK_SIZE_M / 2 - cx) ** 2 + (oy + CHUNK_SIZE_M / 2 - cy) ** 2 }
    return keys.map((k) => ({ k, d: d(k) })).sort((a, b) => a.d - b.d).map((e) => e.k)
  }

  /** Make room for `incoming` trees by dropping the farthest chunks out of view. */
  private evict(incoming: number, keep: Set<number>) {
    let resident = this.residentCount
    if (resident + incoming <= MAX_RESIDENT_POINTS) return
    for (const key of this.byDistance([...this.chunks.keys()]).reverse()) {
      if (resident + incoming <= MAX_RESIDENT_POINTS) break
      if (keep.has(key)) continue
      resident -= this.chunks.get(key)!.count
      this.drop(key)
    }
  }

  private async load(keys: number[]): Promise<boolean> {
    const rowsKey = this.rowsKey!
    const generation = this.generation
    const batch = await this.source.getPointChunks(rowsKey, keys)
    if (generation !== this.generation) return true
    if (!batch || batch.rowsKey !== this.rowsKey) { this.checkKeys = true; return true }
    this.addBatch(batch)
    return true
  }

  private async recolor(keys: number[]): Promise<boolean> {
    const generation = this.generation
    const t0 = performance.now()
    const colors = await this.source.getPointChunkColors(this.rowsKey!, keys)
    if (generation !== this.generation) return true
    if (!colors || colors.rowsKey !== this.rowsKey) { this.checkKeys = true; return true }
    if (colors.colorKey !== this.colorKey) this.colorKey = colors.colorKey
    colors.keys.forEach((key, s) => {
      const chunk = this.chunks.get(key)
      if (!chunk) return
      const next = new Uint8Array(chunk.count)
      for (let k = colors.offsets[s]; k < colors.offsets[s + 1]; k++) {
        const i = chunk.indexByRowid[colors.rowid[k]] ?? -1
        if (i >= 0 && i < chunk.count && chunk.rowid[i] === colors.rowid[k]) next[i] = colors.color[k]
      }
      chunk.color = next
      chunk.palette = colors.palette
      chunk.colorKey = colors.colorKey
      chunk.uploadColors = true
    })
    console.info('[Perf] map:dots:recolor', { chunks: colors.keys.length, ms: Math.round(performance.now() - t0) })
    this.map?.triggerRepaint()
    return true
  }

  /** Swap to new rows: the old chunks go, the new rows' chunks in view arrive together. */
  private install(directory: PointChunkDirectory, known: Map<number, number>, batch: PointChunkBatch | null) {
    this.dropAll()
    this.rowsKey = directory.rowsKey
    this.directory = known
    this.exact = directory.exact
    this.total = directory.counts.reduce((a, b) => a + b, 0)
    this.wantRows = undefined
    this.pendingDirectory = undefined
    if (batch) this.addBatch(batch)
    this.installed = true
    this.map?.triggerRepaint()
    console.info('[Perf] map:dots:rows', { trees: this.total, chunks: known.size, inView: batch?.keys.length ?? 0 })
  }

  private dropAll() {
    for (const chunk of this.chunks.values()) if (chunk.gpu) this.dropped.push(chunk.gpu)
    this.chunks.clear()
    this.empty.clear()
  }

  private drop(key: number) {
    const chunk = this.chunks.get(key)
    if (!chunk) return
    if (chunk.gpu) this.dropped.push(chunk.gpu)
    this.chunks.delete(key)
  }

  private addBatch(batch: PointChunkBatch) {
    const t0 = performance.now()
    batch.keys.forEach((key, s) => {
      const from = batch.offsets[s], to = batch.offsets[s + 1]
      const n = to - from
      this.drop(key)
      // None of the current rows here: remembered, or the view would ask again every pass.
      if (!n) { this.empty.add(key); return }
      this.empty.delete(key)
      // The worker stored the rows in their draw grid's order; these are views, not copies.
      const [originX, originY] = chunkOrigin(key)
      const chunk: Chunk = {
        key, originX, originY, count: n,
        x: batch.x.subarray(from, to),
        y: batch.y.subarray(from, to),
        dbh: batch.dbh.subarray(from, to),
        crown: batch.crown.subarray(from, to),
        color: batch.color.subarray(from, to),
        rowid: batch.rowid.subarray(from, to),
        indexByRowid: new Int32Array(),
        palette: batch.palette, colorKey: batch.colorKey, grid: batch.grids[s],
        uploadPoints: true, uploadColors: true,
      }
      let last = 0
      for (let i = 0; i < n; i++) if (chunk.rowid[i] > last) last = chunk.rowid[i]
      chunk.indexByRowid = new Int32Array(last + 1).fill(-1)
      for (let i = 0; i < n; i++) chunk.indexByRowid[chunk.rowid[i]] = i
      this.chunks.set(key, chunk)
    })
    console.info('[Perf] map:dots:chunks', { chunks: batch.keys.length, rows: batch.x.length, ms: Math.round(performance.now() - t0) })
    this.map?.triggerRepaint()
  }

  // --- Drawing ---

  private upload(gl: GL2, chunk: Chunk) {
    if (!chunk.gpu) {
      chunk.gpu = this.createGpu(gl)
      chunk.uploadPoints = chunk.uploadColors = true
    }
    const gpu = chunk.gpu
    if (chunk.uploadPoints) {
      const pos = new Float32Array(chunk.count * 2)
      for (let i = 0; i < chunk.count; i++) { pos[i * 2] = chunk.x[i]; pos[i * 2 + 1] = chunk.y[i] }
      gl.bindBuffer(gl.ARRAY_BUFFER, gpu.pos)
      gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW)
      chunk.uploadPoints = false
    }
    if (chunk.uploadColors) {
      const attr = new Uint8Array(chunk.count * 4)
      for (let i = 0; i < chunk.count; i++) { attr[i * 4] = chunk.color[i]; attr[i * 4 + 1] = chunk.dbh[i]; attr[i * 4 + 2] = chunk.crown[i] }
      gl.bindBuffer(gl.ARRAY_BUFFER, gpu.attr)
      gl.bufferData(gl.ARRAY_BUFFER, attr, gl.STATIC_DRAW)
      chunk.uploadColors = false
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, null)
  }

  /** The view's bounding box in EPSG:3857 metres, padded by `pad` of its size. A pitched view's box reaches the horizon. */
  private viewBox3857(pad = 0, padPx = 0): Box {
    const map = this.map!
    const b = map.getBounds()
    const [x0, y0] = lngLatTo3857(b.getWest(), b.getSouth())
    const [x1, y1] = lngLatTo3857(b.getEast(), b.getNorth())
    const px = (x1 - x0) * pad + padPx * metresPerPixel(map.getZoom())
    const py = (y1 - y0) * pad + padPx * metresPerPixel(map.getZoom())
    return [x0 - px, y0 - py, x1 + px, y1 + py]
  }

  private chunkBox(chunk: Chunk): Box {
    return [chunk.originX, chunk.originY, chunk.originX + CHUNK_SIZE_M, chunk.originY + CHUNK_SIZE_M]
  }

  private drawRanges(gl: GL2, ranges: number[], offset = 0) {
    for (let k = 0; k < ranges.length; k += 2) gl.drawArrays(gl.POINTS, offset + ranges[k], ranges[k + 1] - ranges[k])
  }

  /** The camera relative to a chunk: the matrix and centre its float32 positions are drawn with. */
  private chunkCamera(matrix: ArrayLike<number>, chunk: Chunk, camX: number, camY: number) {
    const cx = Math.fround(camX - chunk.originX)
    const cy = Math.fround(camY - chunk.originY)
    return { matrix: new Float32Array(relativeMatrix(matrix, chunk.originX + cx, chunk.originY + cy)), cx, cy }
  }

  private camera() {
    const map = this.map!
    const centre = map.getCenter()
    const [camX, camY] = lngLatTo3857(centre.lng, centre.lat)
    const canvas = map.getCanvas()
    return {
      camX, camY,
      zoom: map.getZoom(),
      camdist: map.transform.cameraToCenterDistance,
      width: canvas.clientWidth,
      height: canvas.clientHeight,
      dpr: canvas.width / Math.max(1, canvas.clientWidth),
    }
  }

  prerender(gl: WebGLRenderingContext | WebGL2RenderingContext) {
    const g = gl as GL2
    this.freeDropped(g)
    if (!this.map || !this.circleProgram) return
    for (const chunk of this.chunks.values()) this.upload(g, chunk)
  }

  render(gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: Parameters<CustomLayerInterface['render']>[1]) {
    if (!this.map || !this.circleProgram) return
    const g = gl as GL2
    const zoom = this.map.getZoom()
    const cam = this.camera()
    const radius = circleRadiusRange(zoom)
    const pxPerMetre = pixelsPerMetre(zoom, this.map.getCenter().lat)
    this.frame = { matrix: Float64Array.from(matrix as ArrayLike<number>), width: cam.width, height: cam.height, camdist: cam.camdist, radius, pxPerMetre }
    if (!this.chunks.size) return
    g.disable(g.DEPTH_TEST)
    g.disable(g.STENCIL_TEST)
    g.enable(g.BLEND)
    g.blendFunc(g.ONE, g.ONE_MINUS_SRC_ALPHA)

    const opacity = circleOpacity(zoom)
    const coverage = this.spriteCoverage()
    if (opacity > 0 || coverage) {
      g.useProgram(this.circleProgram)
      g.uniform2f(this.circleU.u_radius, radius[0], radius[1])
      g.uniform1f(this.circleU.u_dpr, cam.dpr)
      g.uniform1f(this.circleU.u_camdist, cam.camdist)
      g.uniform1f(this.circleU.u_px_per_m, pxPerMetre)
      g.uniform1f(this.circleU.u_opacity, opacity)
      g.uniform1f(this.circleU.u_reach, coverage === null ? -1 : Number.isFinite(coverage.reach) ? coverage.reach : 1e9)
      const polygon = coverage?.polygon ?? []
      const corners = polygon.length >= 3 ? [0, 1, 2, 3].map(i => polygon[Math.min(i, polygon.length - 1)]) : []
      const poly = new Float32Array(8)
      g.uniform1f(this.circleU.u_far_opacity, CIRCLE_FAR_OPACITY)
      // Reach is a disc around where the batch was queried, not around the camera now.
      const eye = coverage?.eye ?? { lng: 0, lat: 0, altitude: 0 }
      const [eyeX, eyeY] = lngLatTo3857(eye.lng, eye.lat)
      g.uniform1f(this.circleU.u_altitude, eye.altitude)
      g.uniform1f(this.circleU.u_scale, Math.cos(this.map.getCenter().lat * Math.PI / 180))
      g.activeTexture(g.TEXTURE0)
      g.uniform1i(this.circleU.u_palette, 0)
      const view = this.viewBox3857(0, maxDotRadius(radius, pxPerMetre) + 2)
      for (const chunk of this.chunks.values()) {
        if (!chunk.gpu || !intersects(this.chunkBox(chunk), view)) continue
        const local = this.chunkCamera(matrix as ArrayLike<number>, chunk, cam.camX, cam.camY)
        g.uniformMatrix4fv(this.circleU.u_matrix, false, local.matrix)
        g.uniform2f(this.circleU.u_center, local.cx, local.cy)
        g.uniform2f(this.circleU.u_eye, eyeX - chunk.originX, eyeY - chunk.originY)
        corners.forEach(([x, y], i) => { poly[i * 2] = x - chunk.originX; poly[i * 2 + 1] = y - chunk.originY })
        g.uniform2fv(this.circleU.u_poly, poly)
        g.bindTexture(g.TEXTURE_2D, this.paletteTexture(g, chunk.palette))
        g.bindVertexArray(chunk.gpu.pointVao)
        this.drawRanges(g, visibleRanges(chunk.grid,
          view[0] - chunk.originX, view[1] - chunk.originY, view[2] - chunk.originX, view[3] - chunk.originY))
      }
      g.bindTexture(g.TEXTURE_2D, null)
    }
    g.bindVertexArray(null)
  }

  // --- Picking ---

  /** True while the circles are drawn, the zooms a tap selects a tree at. */
  pickable(): boolean {
    const zoom = this.map?.getZoom() ?? 0
    return this.chunks.size > 0 && zoom >= CIRCLE_PICK_MIN_ZOOM && zoom < CIRCLE_MAX_ZOOM
  }

  /** A chunk's point `i` in CSS pixels as last frame drew it, with its depth (w); null behind the camera. */
  private project(chunk: Chunk, rel: Float64Array, i: number): { x: number; y: number; w: number } | null {
    const frame = this.frame!
    const dx = chunk.x[i], dy = chunk.y[i]
    const w = rel[3] * dx + rel[7] * dy + rel[15]
    if (w <= 0) return null
    return {
      x: ((rel[0] * dx + rel[4] * dy + rel[12]) / w + 1) * 0.5 * frame.width,
      y: (1 - (rel[1] * dx + rel[5] * dy + rel[13]) / w) * 0.5 * frame.height,
      w,
    }
  }

  /** Chunks within `reach` metres of an EPSG:3857 point, nearest first. */
  private chunksNear(x: number, y: number, reach: number): Chunk[] {
    return chunksInBox(x - reach, y - reach, x + reach, y + reach).flatMap((k) => this.chunks.get(k) ?? [])
  }

  /**
   * Up to `max` circles drawn last frame inside the canvas, as CSS-pixel
   * centres, the middle of the view first: diagnostics and tests, since a
   * custom layer has no queryRenderedFeatures.
   */
  visibleTrees(max = 200): { x: number; y: number; rowid: number }[] {
    if (!this.map || !this.frame || !this.pickable()) return []
    const frame = this.frame
    const out: { x: number; y: number; rowid: number }[] = []
    const seen = new Set<string>()
    const [x0, y0, x1, y1] = this.viewBox3857()
    const mx = (x0 + x1) / 2, my = (y0 + y1) / 2
    const reach = Math.max(x1 - x0, y1 - y0) / 2
    for (let r = reach / 16; out.length < max; r *= 2) {
      for (const chunk of this.chunksNear(mx, my, r)) {
        const rel = relativeMatrix(frame.matrix, chunk.originX, chunk.originY)
        pointsNear(chunk.grid, mx - chunk.originX, my - chunk.originY, r, (i) => {
          const id = `${chunk.key}:${i}`
          if (out.length >= max || seen.has(id)) return
          seen.add(id)
          const p = this.project(chunk, rel, i)
          if (p && p.x >= 0 && p.y >= 0 && p.x <= frame.width && p.y <= frame.height) out.push({ x: p.x, y: p.y, rowid: chunk.rowid[i] })
        })
      }
      if (r >= reach) break
    }
    return out
  }

  /** The circle drawn under a CSS-pixel point, nearest centre first, as last frame drew it. */
  pick(point: { x: number; y: number }, tolerancePx = 2): TreeDotsPick | undefined {
    const frame = this.frame
    if (!this.map || !frame || !this.pickable()) return undefined
    const at = this.map.unproject([point.x, point.y])
    const [qx, qy] = lngLatTo3857(at.lng, at.lat)
    // Generous in metres (a pitched view stretches the far side); the test below is in pixels.
    const reach = (maxDotRadius(frame.radius, frame.pxPerMetre) + tolerancePx) * metresPerPixel(this.map.getZoom()) * 4
    let best: { chunk: Chunk; i: number } | undefined
    let bestDistance = Infinity
    for (const chunk of this.chunksNear(qx, qy, reach)) {
      const rel = relativeMatrix(frame.matrix, chunk.originX, chunk.originY)
      pointsNear(chunk.grid, qx - chunk.originX, qy - chunk.originY, reach, (i) => {
        const p = this.project(chunk, rel, i)
        if (!p) return
        const distance = Math.hypot(p.x - point.x, p.y - point.y)
        const r = (dotRadius(chunk.dbh[i], chunk.crown[i], frame.radius, frame.pxPerMetre) + 0.5) * (frame.camdist / p.w)
        if (distance <= r + tolerancePx && distance < bestDistance) { best = { chunk, i }; bestDistance = distance }
      })
    }
    if (!best) return undefined
    const { chunk, i } = best
    const [lng, lat] = lngLatFrom3857(chunk.x[i] + chunk.originX, chunk.y[i] + chunk.originY)
    return { key: chunk.key, rowid: chunk.rowid[i], lng, lat }
  }
}

