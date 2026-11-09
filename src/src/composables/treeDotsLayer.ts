import type { CustomLayerInterface, Map as MapLibreMap } from 'maplibre-gl'
import {
  CHUNK_SIZE_M, chunkOrigin, chunksInBox,
  type ChunkGrid, type PointChunkBatch, type PointChunkColors, type PointChunkDirectory, type PointKeys,
} from '../workers/cityPoints'
import {
  CIRCLE_MAX_ZOOM, CIRCLE_PICK_MIN_ZOOM, HEAT_ENABLED, HEAT_MAX_ZOOM, HEAT_MIN_ZOOM,
  CIRCLE_FAR_OPACITY, circleOpacity, visibleRanges, circleRadius, circleRadiusRange,
  heatIntensity, heatOpacity, heatRadius, hexRgb, levelForKernel, lngLatFrom3857, lngLatTo3857, metresPerPixel,
  pointsNear, relativeMatrix,
} from '../lib/treeDots'
import { REACH_FADE } from '../lib/treeCrowns'
import TreeDotsIndexWorker from '../workers/treeDotsIndex.worker?worker'
import type { DotsIndexRequest, DotsIndexResult } from '../workers/treeDotsIndex.worker'

/**
 * The desktop map's trees below the sprites: a density heat (z6 to z15) and
 * trunk-sized circles (z12.8 to z15.5), drawn from typed arrays on the GPU.
 * It replaces the vector-tile source and its per-colour heatmap and circle
 * layers on the desktop (docs/MAP_COLOR_PERF_AUDIT.md):
 *
 * - The trees arrive in chunks of one global grid (workers/cityPoints.ts:
 *   Web Mercator tiles at z12), never as a city: the chunks in view first,
 *   then the nearest others in the background up to a point budget, the
 *   farthest dropped past it. Each chunk carries its own origin, buffers,
 *   heat pyramid and pick grid, so nothing here knows which city it is in.
 * - Panning and zooming within what is loaded never waits: a frame draws
 *   only the ranges of the chunks in view.
 * - The heat is two passes whatever the palette: trees add their colour and
 *   weight into a quarter-resolution float target, and a resolve pass draws
 *   the weighted mean colour with an alpha from the density. It is summed on
 *   screen, so chunks meet without seams.
 * - A colour change re-reads one byte per tree, chunks in view first, and
 *   swaps it in; the old colours stay drawn until then. A query or filter
 *   change keeps the old trees drawn until the new rows in view arrive.
 *
 * Needs WebGL2 with a renderable float target; `TreeDotsLayer.supported`
 * says so before the map adds it, and the vector tiles remain otherwise.
 */

type GL2 = WebGL2RenderingContext

export interface TreeDotsSource {
  getPointKeys: () => Promise<PointKeys | null>
  getPointChunkDirectory: () => Promise<PointChunkDirectory | null>
  getPointChunks: (rowsKey: string, keys: number[]) => Promise<PointChunkBatch | null>
  getPointChunkColors: (rowsKey: string, keys: number[]) => Promise<PointChunkColors | null>
}

export interface TreeDotsPick {
  rowid: number
  lng: number
  lat: number
}

/**
 * The heat's alpha ramp, in log2 of the density (trees under a kernel,
 * weighted by its falloff): faint at 8, full colour at 128. Tuned on Boston
 * against the per-colour heatmaps it replaced, so a street of trees reads
 * and the blocks, water and parks between them stay clear.
 */
const HEAT_ALPHA_LOG2: [number, number] = [3, 7]
/** A mean of shades drifts toward grey; the resolve lifts saturation by this. */
const HEAT_SATURATION = 1.2
const HEAT_SCALE = 4

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

function uniforms(gl: GL2, program: WebGLProgram, names: string[]): Record<string, WebGLUniformLocation | null> {
  return Object.fromEntries(names.map((n) => [n, gl.getUniformLocation(program, n)]))
}

const HEAT_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec2 a_pos;
layout(location = 1) in float a_weight;
layout(location = 2) in vec4 a_color;
uniform mat4 u_matrix;
uniform vec2 u_center;
uniform float u_size;
uniform float u_camdist;
out vec4 v_color;
out float v_weight;
void main() {
  gl_Position = u_matrix * vec4(a_pos - u_center, 0.0, 1.0);
  gl_PointSize = gl_Position.w > 0.0 ? clamp(u_size * u_camdist / gl_Position.w, 1.0, 128.0) : 0.0;
  v_color = a_color;
  v_weight = a_weight;
}`

// Two targets: the colour-weighted colour sum with its weight, and the
// density. A cell's a_color.a is its colour weight per tree (colorWeight).
const HEAT_FS = `#version 300 es
precision highp float;
in vec4 v_color;
in float v_weight;
uniform float u_intensity;
layout(location = 0) out vec4 o_color;
layout(location = 1) out vec4 o_density;
void main() {
  vec2 d = gl_PointCoord * 2.0 - 1.0;
  float r2 = dot(d, d);
  if (r2 > 1.0) discard;
  float k = exp(-4.5 * r2) * v_weight * u_intensity;
  float ck = k * v_color.a;
  o_color = vec4(v_color.rgb * ck, ck);
  o_density = vec4(k, 0.0, 0.0, 0.0);
}`

const RESOLVE_VS = `#version 300 es
precision highp float;
out vec2 v_uv;
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  v_uv = p;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`

const RESOLVE_FS = `#version 300 es
precision highp float;
in vec2 v_uv;
uniform sampler2D u_color;
uniform sampler2D u_density;
uniform float u_opacity;
uniform vec2 u_alpha;
uniform float u_saturation;
out vec4 o;
void main() {
  float density = texture(u_density, v_uv).r;
  if (density <= 0.0001) discard;
  vec4 s = texture(u_color, v_uv);
  vec3 c = s.rgb / max(s.a, 1e-6);
  c = clamp(mix(vec3(dot(c, vec3(0.299, 0.587, 0.114))), c, u_saturation), 0.0, 1.0);
  // Density in log steps: a lone tree is a faint wash, a planted street full colour.
  float a = smoothstep(u_alpha.x, u_alpha.y, log2(density)) * u_opacity;
  o = vec4(c * a, a);
}`

const CIRCLE_VS = `#version 300 es
precision highp float;
layout(location = 0) in vec2 a_pos;
layout(location = 1) in vec2 a_attr;
uniform mat4 u_matrix;
uniform vec2 u_center;
uniform vec2 u_radius;
uniform float u_dpr;
uniform float u_camdist;
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
  float radius = mix(u_radius.x, u_radius.y, s) * u_dpr * (gl_Position.w > 0.0 ? u_camdist / gl_Position.w : 0.0);
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
  levelPos: WebGLBuffer
  levelColor: WebGLBuffer
  pointVao: WebGLVertexArrayObject
  levelVao: WebGLVertexArrayObject
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
  color: Uint8Array
  rowid: Uint32Array
  palette: string[]
  colorKey: string
  /** 512 m cells over the points: the draw ranges and the pick index. */
  grid: ChunkGrid
  /** The heat pyramid, from the indexer; empty until it arrives. */
  levels: { cell: number; count: number; grid: ChunkGrid }[]
  levelOffsets: number[]
  levelPositions?: Float32Array
  levelColors?: Uint8Array
  buildId: number
  colorId: number
  gpu?: ChunkGpu
  uploadPoints: boolean
  uploadColors: boolean
  uploadLevels: boolean
  uploadLevelColors: boolean
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
  private heatProgram?: WebGLProgram
  private resolveProgram?: WebGLProgram
  private circleProgram?: WebGLProgram
  private heatU: Record<string, WebGLUniformLocation | null> = {}
  private resolveU: Record<string, WebGLUniformLocation | null> = {}
  private circleU: Record<string, WebGLUniformLocation | null> = {}
  private resolveVao?: WebGLVertexArrayObject
  private palettes = new Map<string, WebGLTexture>()
  private fbo?: WebGLFramebuffer
  private colorSum?: WebGLTexture
  private density?: WebGLTexture
  private fboSize: [number, number] = [0, 0]
  private heatDrawn = false
  private dropped: ChunkGpu[] = []

  // --- What is drawn ---
  private chunks = new Map<number, Chunk>()
  /** The installed rows: their identity, non-empty chunks and tree count. */
  private rowsKey?: string
  private directory = new Map<number, number>()
  private total = 0
  private installed = false
  /** rowid to its index within its chunk, for the installed rows. */
  private indexByRowid = new Int32Array()
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
  private indexer?: Worker
  private indexSeq = 0

  /** Last frame's camera, for picking exactly what was drawn. */
  private frame?: { matrix: Float64Array; width: number; height: number; camdist: number; radius: [number, number] }

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

  /** WebGL2 with a renderable half-float target: the heat accumulates in one. */
  static supported(map: MapLibreMap): boolean {
    const gl = (map as unknown as { painter?: { context?: { gl?: unknown } } }).painter?.context?.gl
    if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) return false
    return !!(gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float'))
  }

  /** Trees in the current rows, loaded or not; 0 until the rows are known. */
  get count(): number { return this.total }
  /** Trees held in memory. */
  get residentCount(): number { let n = 0; for (const c of this.chunks.values()) n += c.count; return n }
  get chunkCount(): number { return this.chunks.size }
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
    this.indexer?.terminate()
    this.indexer = undefined
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
    chunk.uploadLevels = !!chunk.levelPositions
    chunk.uploadLevelColors = !!chunk.levelColors
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
    for (const p of [this.heatProgram, this.resolveProgram, this.circleProgram]) if (p) gl.deleteProgram(p)
    if (this.resolveVao) gl.deleteVertexArray(this.resolveVao)
    for (const t of [...this.palettes.values(), this.colorSum, this.density]) if (t) gl.deleteTexture(t)
    this.palettes.clear()
    if (this.fbo) gl.deleteFramebuffer(this.fbo)
    this.fbo = this.colorSum = this.density = undefined
    this.fboSize = [0, 0]
  }

  private freeDropped(gl: GL2) {
    for (const gpu of this.dropped) {
      for (const b of [gpu.pos, gpu.attr, gpu.levelPos, gpu.levelColor]) gl.deleteBuffer(b)
      gl.deleteVertexArray(gpu.pointVao)
      gl.deleteVertexArray(gpu.levelVao)
    }
    this.dropped = []
  }

  private initialize() {
    const gl = this.gl!
    if (!gl.getExtension('EXT_color_buffer_float')) gl.getExtension('EXT_color_buffer_half_float')
    this.heatProgram = compile(gl, HEAT_VS, HEAT_FS, 'Heat')
    this.resolveProgram = compile(gl, RESOLVE_VS, RESOLVE_FS, 'Heat resolve')
    this.circleProgram = compile(gl, CIRCLE_VS, CIRCLE_FS, 'Circle')
    this.heatU = uniforms(gl, this.heatProgram, ['u_matrix', 'u_center', 'u_size', 'u_camdist', 'u_intensity'])
    this.resolveU = uniforms(gl, this.resolveProgram, ['u_color', 'u_density', 'u_opacity', 'u_alpha', 'u_saturation'])
    this.circleU = uniforms(gl, this.circleProgram, ['u_matrix', 'u_center', 'u_radius', 'u_dpr', 'u_camdist', 'u_palette', 'u_opacity', 'u_reach', 'u_far_opacity', 'u_altitude', 'u_scale', 'u_eye', 'u_poly'])
    this.resolveVao = gl.createVertexArray()!
  }

  private createGpu(gl: GL2): ChunkGpu {
    const gpu: ChunkGpu = {
      pos: gl.createBuffer()!, attr: gl.createBuffer()!, levelPos: gl.createBuffer()!, levelColor: gl.createBuffer()!,
      pointVao: gl.createVertexArray()!, levelVao: gl.createVertexArray()!,
    }
    gl.bindVertexArray(gpu.pointVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.pos)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.attr)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 2, gl.UNSIGNED_BYTE, false, 2, 0)
    gl.bindVertexArray(gpu.levelVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.levelPos)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 12, 0)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 12, 8)
    gl.bindBuffer(gl.ARRAY_BUFFER, gpu.levelColor)
    gl.enableVertexAttribArray(2)
    gl.vertexAttribPointer(2, 4, gl.UNSIGNED_BYTE, true, 4, 0)
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
      const visible = this.inView(known)
      const batch = visible.length ? await this.source.getPointChunks(directory.rowsKey, visible) : null
      if (stale()) return true
      if (visible.length && !batch) { this.checkKeys = true; return true }
      this.install(directory, known, batch)
      return true
    }
    if (!this.installed || !this.rowsKey || !this.map) return false
    const visible = this.inView(this.directory)
    const missing = visible.filter((k) => !this.chunks.has(k))
    if (missing.length) {
      this.evict(missing.reduce((n, k) => n + (this.directory.get(k) ?? 0), 0), new Set(visible))
      return this.load(missing)
    }
    const inView = new Set(visible)
    const offColor = [...this.chunks.values()].filter((c) => c.colorKey !== this.colorKey)
    const offInView = offColor.filter((c) => inView.has(c.key))
    if (offColor.length) return this.recolor((offInView.length ? offInView : offColor).map((c) => c.key))
    // The view is current: what a sync waits on.
    this.settle()
    // The rest of the city is background work: it yields to a moving camera and to sprite queries.
    const now = performance.now()
    if ((this.map.isMoving() || this.spritesBusy()) && now - (this.deferredSince ??= now) < PREFETCH_MAX_DEFER_MS) {
      clearTimeout(this.prefetchTimer)
      this.prefetchTimer = setTimeout(this.schedule, PREFETCH_RETRY_MS)
      return false
    }
    this.deferredSince = undefined
    const resident = this.residentCount
    const rest: number[] = []
    let batch = 0
    for (const key of this.byDistance([...this.directory.keys()].filter((k) => !this.chunks.has(k)))) {
      const n = this.directory.get(key) ?? 0
      if (resident + batch + n > MAX_RESIDENT_POINTS) break
      if (batch && batch + n > PREFETCH_BATCH_POINTS) break
      rest.push(key)
      batch += n
    }
    return rest.length ? this.load(rest) : false
  }

  /** The keys of `known` chunks in the padded view, nearest the centre first. */
  private inView(known: Map<number, number>): number[] {
    const [x0, y0, x1, y1] = this.viewBox3857(VIEW_PAD)
    return chunksInBox(x0, y0, x1, y1).filter((k) => known.has(k))
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
        const i = this.indexByRowid[colors.rowid[k]]
        if (i >= 0 && i < chunk.count && chunk.rowid[i] === colors.rowid[k]) next[i] = colors.color[k]
      }
      chunk.color = next
      chunk.palette = colors.palette
      chunk.colorKey = colors.colorKey
      chunk.uploadColors = true
      chunk.colorId = ++this.indexSeq
      const copy = next.slice()
      this.index({ kind: 'recolor', id: chunk.colorId, key, color: copy, palette: colors.palette }, [copy.buffer])
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
    this.total = directory.counts.reduce((a, b) => a + b, 0)
    this.indexByRowid = new Int32Array(directory.maxRowid + 1).fill(-1)
    this.wantRows = undefined
    this.pendingDirectory = undefined
    if (batch) this.addBatch(batch)
    this.installed = true
    this.map?.triggerRepaint()
    console.info('[Perf] map:dots:rows', { trees: this.total, chunks: known.size, inView: batch?.keys.length ?? 0 })
    this.onLoaded?.(this.total)
  }

  private dropAll() {
    for (const chunk of this.chunks.values()) if (chunk.gpu) this.dropped.push(chunk.gpu)
    this.chunks.clear()
    this.indexer?.postMessage({ kind: 'clear' } satisfies DotsIndexRequest)
  }

  private drop(key: number) {
    const chunk = this.chunks.get(key)
    if (!chunk) return
    if (chunk.gpu) this.dropped.push(chunk.gpu)
    this.chunks.delete(key)
    this.indexer?.postMessage({ kind: 'drop', key } satisfies DotsIndexRequest)
  }

  private addBatch(batch: PointChunkBatch) {
    const t0 = performance.now()
    batch.keys.forEach((key, s) => {
      const from = batch.offsets[s], to = batch.offsets[s + 1]
      const n = to - from
      if (!n) return
      this.drop(key)
      // The worker stored the rows in their draw grid's order; these are views, not copies.
      const [originX, originY] = chunkOrigin(key)
      const chunk: Chunk = {
        key, originX, originY, count: n,
        x: batch.x.subarray(from, to),
        y: batch.y.subarray(from, to),
        dbh: batch.dbh.subarray(from, to),
        color: batch.color.subarray(from, to),
        rowid: batch.rowid.subarray(from, to),
        palette: batch.palette, colorKey: batch.colorKey, grid: batch.grids[s],
        levels: [], levelOffsets: [],
        buildId: 0, colorId: 0,
        uploadPoints: true, uploadColors: true, uploadLevels: false, uploadLevelColors: false,
      }
      for (let i = 0; i < n; i++) if (chunk.rowid[i] < this.indexByRowid.length) this.indexByRowid[chunk.rowid[i]] = i
      chunk.buildId = chunk.colorId = ++this.indexSeq
      const x = chunk.x.slice(), y = chunk.y.slice(), color = chunk.color.slice()
      this.index({ kind: 'build', id: chunk.buildId, key, x, y, count: n, color, palette: chunk.palette }, [x.buffer, y.buffer, color.buffer])
      this.chunks.set(key, chunk)
    })
    console.info('[Perf] map:dots:chunks', { chunks: batch.keys.length, rows: batch.x.length, ms: Math.round(performance.now() - t0) })
    this.map?.triggerRepaint()
  }

  private index(request: DotsIndexRequest, transfer: Transferable[]) {
    if (!this.indexer) {
      this.indexer = new TreeDotsIndexWorker({ name: 'tree-dots-index' })
      this.indexer.onmessage = (event: MessageEvent<DotsIndexResult>) => this.onIndexed(event.data)
    }
    this.indexer.postMessage(request, transfer)
  }

  private onIndexed(result: DotsIndexResult) {
    const chunk = this.chunks.get(result.key)
    if (!chunk) return
    if (result.positions && result.counts && result.cells && result.grids) {
      if (result.id !== chunk.buildId) return
      chunk.levels = result.cells.map((cell, i) => ({ cell, count: result.counts![i], grid: result.grids![i] }))
      chunk.levelOffsets = []
      let offset = 0
      for (const level of chunk.levels) { chunk.levelOffsets.push(offset); offset += level.count }
      chunk.levelPositions = result.positions
      chunk.uploadLevels = true
    }
    if (result.id !== chunk.colorId) return
    chunk.levelColors = result.colors
    chunk.uploadLevelColors = true
    this.map?.triggerRepaint()
  }

  // --- Drawing ---

  private upload(gl: GL2, chunk: Chunk) {
    if (!chunk.gpu) {
      chunk.gpu = this.createGpu(gl)
      chunk.uploadPoints = chunk.uploadColors = true
      chunk.uploadLevels = !!chunk.levelPositions
      chunk.uploadLevelColors = !!chunk.levelColors
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
      const attr = new Uint8Array(chunk.count * 2)
      for (let i = 0; i < chunk.count; i++) { attr[i * 2] = chunk.color[i]; attr[i * 2 + 1] = chunk.dbh[i] }
      gl.bindBuffer(gl.ARRAY_BUFFER, gpu.attr)
      gl.bufferData(gl.ARRAY_BUFFER, attr, gl.STATIC_DRAW)
      chunk.uploadColors = false
    }
    if (chunk.uploadLevels && chunk.levelPositions) {
      gl.bindBuffer(gl.ARRAY_BUFFER, gpu.levelPos)
      gl.bufferData(gl.ARRAY_BUFFER, chunk.levelPositions, gl.STATIC_DRAW)
      chunk.uploadLevels = false
    }
    if (chunk.uploadLevelColors && chunk.levelColors) {
      gl.bindBuffer(gl.ARRAY_BUFFER, gpu.levelColor)
      gl.bufferData(gl.ARRAY_BUFFER, chunk.levelColors, gl.STATIC_DRAW)
      chunk.uploadLevelColors = false
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

  prerender(gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: Parameters<CustomLayerInterface['render']>[1]) {
    this.heatDrawn = false
    const g = gl as GL2
    this.freeDropped(g)
    if (!this.map || !this.chunks.size || !this.heatProgram) return
    for (const chunk of this.chunks.values()) this.upload(g, chunk)
    const zoom = this.map.getZoom()
    if (!HEAT_ENABLED || zoom < HEAT_MIN_ZOOM || zoom >= HEAT_MAX_ZOOM || heatOpacity(zoom) <= 0) return
    const cam = this.camera()
    const w = Math.max(1, Math.ceil(g.drawingBufferWidth / HEAT_SCALE))
    const h = Math.max(1, Math.ceil(g.drawingBufferHeight / HEAT_SCALE))
    this.ensureTarget(g, w, h)

    const radiusPx = heatRadius(zoom)
    const kernelM = radiusPx * metresPerPixel(zoom)
    const view = this.viewBox3857(0, radiusPx * 1.5)

    g.bindFramebuffer(g.FRAMEBUFFER, this.fbo!)
    g.viewport(0, 0, w, h)
    g.disable(g.DEPTH_TEST)
    g.disable(g.STENCIL_TEST)
    g.disable(g.SCISSOR_TEST)
    g.disable(g.CULL_FACE)
    g.colorMask(true, true, true, true)
    g.clearBufferfv(g.COLOR, 0, [0, 0, 0, 0])
    g.clearBufferfv(g.COLOR, 1, [0, 0, 0, 0])
    g.enable(g.BLEND)
    g.blendFunc(g.ONE, g.ONE)
    g.useProgram(this.heatProgram)
    g.uniform1f(this.heatU.u_size, (2 * radiusPx * cam.dpr) / HEAT_SCALE)
    g.uniform1f(this.heatU.u_camdist, cam.camdist)
    g.uniform1f(this.heatU.u_intensity, heatIntensity(zoom))
    for (const chunk of this.chunks.values()) {
      // The pyramid and its colours arrive from the indexer; until both have, the chunk adds no heat.
      if (!chunk.levels.length || !chunk.levelColors || chunk.uploadLevels || !chunk.gpu) continue
      if (!intersects(this.chunkBox(chunk), view)) continue
      const level = levelForKernel(chunk.levels, kernelM)
      const local = this.chunkCamera(matrix as ArrayLike<number>, chunk, cam.camX, cam.camY)
      g.uniformMatrix4fv(this.heatU.u_matrix, false, local.matrix)
      g.uniform2f(this.heatU.u_center, local.cx, local.cy)
      g.bindVertexArray(chunk.gpu.levelVao)
      const ranges = visibleRanges(chunk.levels[level].grid,
        view[0] - chunk.originX, view[1] - chunk.originY, view[2] - chunk.originX, view[3] - chunk.originY)
      this.drawRanges(g, ranges, chunk.levelOffsets[level])
    }
    g.bindVertexArray(null)
    g.bindFramebuffer(g.FRAMEBUFFER, null)
    g.viewport(0, 0, g.drawingBufferWidth, g.drawingBufferHeight)
    this.heatDrawn = true
  }

  private targetTexture(gl: GL2, texture: WebGLTexture | undefined, internal: number, format: number, w: number, h: number): WebGLTexture {
    const t = texture ?? gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, t)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, w, h, 0, format, gl.HALF_FLOAT, null)
    gl.bindTexture(gl.TEXTURE_2D, null)
    return t
  }

  private ensureTarget(gl: GL2, w: number, h: number) {
    if (this.fbo && this.fboSize[0] === w && this.fboSize[1] === h) return
    this.colorSum = this.targetTexture(gl, this.colorSum, gl.RGBA16F, gl.RGBA, w, h)
    this.density = this.targetTexture(gl, this.density, gl.R16F, gl.RED, w, h)
    if (!this.fbo) this.fbo = gl.createFramebuffer()!
    gl.bindFramebuffer(gl.FRAMEBUFFER, this.fbo)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, this.colorSum, 0)
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT1, gl.TEXTURE_2D, this.density, 0)
    gl.drawBuffers([gl.COLOR_ATTACHMENT0, gl.COLOR_ATTACHMENT1])
    gl.bindFramebuffer(gl.FRAMEBUFFER, null)
    this.fboSize = [w, h]
  }

  render(gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: Parameters<CustomLayerInterface['render']>[1]) {
    if (!this.map || !this.circleProgram) return
    const g = gl as GL2
    const zoom = this.map.getZoom()
    const cam = this.camera()
    const radius = circleRadiusRange(zoom)
    this.frame = { matrix: Float64Array.from(matrix as ArrayLike<number>), width: cam.width, height: cam.height, camdist: cam.camdist, radius }
    if (!this.chunks.size) return
    g.disable(g.DEPTH_TEST)
    g.disable(g.STENCIL_TEST)
    g.enable(g.BLEND)
    g.blendFunc(g.ONE, g.ONE_MINUS_SRC_ALPHA)

    if (this.heatDrawn) {
      g.useProgram(this.resolveProgram!)
      g.activeTexture(g.TEXTURE0)
      g.bindTexture(g.TEXTURE_2D, this.colorSum!)
      g.uniform1i(this.resolveU.u_color, 0)
      g.activeTexture(g.TEXTURE1)
      g.bindTexture(g.TEXTURE_2D, this.density!)
      g.uniform1i(this.resolveU.u_density, 1)
      g.uniform1f(this.resolveU.u_opacity, heatOpacity(zoom))
      g.uniform2f(this.resolveU.u_alpha, HEAT_ALPHA_LOG2[0], HEAT_ALPHA_LOG2[1])
      g.uniform1f(this.resolveU.u_saturation, HEAT_SATURATION)
      g.bindVertexArray(this.resolveVao!)
      g.drawArrays(g.TRIANGLES, 0, 3)
      g.bindTexture(g.TEXTURE_2D, null)
      g.activeTexture(g.TEXTURE0)
      g.bindTexture(g.TEXTURE_2D, null)
    }

    const opacity = circleOpacity(zoom)
    const coverage = this.spriteCoverage()
    if (opacity > 0 || coverage) {
      g.useProgram(this.circleProgram)
      g.uniform2f(this.circleU.u_radius, radius[0], radius[1])
      g.uniform1f(this.circleU.u_dpr, cam.dpr)
      g.uniform1f(this.circleU.u_camdist, cam.camdist)
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
      const view = this.viewBox3857(0, radius[1] + 2)
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
    const reach = (frame.radius[1] + tolerancePx) * metresPerPixel(this.map.getZoom()) * 4
    let best: { chunk: Chunk; i: number } | undefined
    let bestDistance = Infinity
    for (const chunk of this.chunksNear(qx, qy, reach)) {
      const rel = relativeMatrix(frame.matrix, chunk.originX, chunk.originY)
      pointsNear(chunk.grid, qx - chunk.originX, qy - chunk.originY, reach, (i) => {
        const p = this.project(chunk, rel, i)
        if (!p) return
        const distance = Math.hypot(p.x - point.x, p.y - point.y)
        const r = (circleRadius(chunk.dbh[i], frame.radius) + 0.5) * (frame.camdist / p.w)
        if (distance <= r + tolerancePx && distance < bestDistance) { best = { chunk, i }; bestDistance = distance }
      })
    }
    if (!best) return undefined
    const { chunk, i } = best
    const [lng, lat] = lngLatFrom3857(chunk.x[i] + chunk.originX, chunk.y[i] + chunk.originY)
    return { rowid: chunk.rowid[i], lng, lat }
  }
}

