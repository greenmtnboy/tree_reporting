import type { CustomLayerInterface, Map as MapLibreMap } from 'maplibre-gl'
import type { CityPointColors, CityPoints } from '../workers/cityPoints'
import {
  CIRCLE_MAX_ZOOM, CIRCLE_MIN_ZOOM, HEAT_MAX_ZOOM, HEAT_MIN_ZOOM,
  DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M, buildPickGrid, circleOpacity, visibleRanges, circleRadius, circleRadiusRange, heatIntensity, heatOpacity,
  heatRadius, hexRgb, levelForKernel, lngLatFrom3857, lngLatTo3857, metresPerPixel, pointsNear,
  relativeMatrix, type PickGrid,
} from '../lib/treeDots'
import TreeDotsIndexWorker from '../workers/treeDotsIndex.worker?worker'
import type { DotsIndexRequest, DotsIndexResult } from '../workers/treeDotsIndex.worker'

/**
 * The desktop map's trees below the sprites: a density heat (z6 to z15) and
 * trunk-sized circles (z12.8 to z15.5), drawn from the whole city's points
 * held on the GPU. It replaces the vector-tile source and its per-colour
 * heatmap and circle layers on the desktop (docs/MAP_COLOR_PERF_AUDIT.md):
 *
 * - Panning and zooming never wait on tiles: every tree is resident.
 * - The heat is two passes whatever the palette: trees add their colour and
 *   weight into a quarter-resolution float target, and a resolve pass draws
 *   the weighted mean colour with an alpha from the density. The pyramid in
 *   lib/treeDots.ts keeps the far zooms to a few tens of thousands of cells.
 * - A colour change re-reads one byte per tree and swaps it in; the old
 *   colours stay drawn until then.
 *
 * Needs WebGL2 with a renderable float target; `TreeDotsLayer.supported`
 * says so before the map adds it, and the vector tiles remain otherwise.
 */

type GL2 = WebGL2RenderingContext

export interface TreeDotsSource {
  getCityPoints: () => Promise<CityPoints | null>
  getCityPointColors: (rowsKey: string) => Promise<CityPointColors | null>
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
out vec3 v_color;
out float v_radius;
void main() {
  gl_Position = u_matrix * vec4(a_pos - u_center, 0.0, 1.0);
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
uniform float u_opacity;
out vec4 o;
void main() {
  float d = length(gl_PointCoord - 0.5) * (2.0 * v_radius + 2.0);
  float a = clamp(v_radius - d + 0.5, 0.0, 1.0) * u_opacity;
  if (a <= 0.0) discard;
  o = vec4(v_color * a, a);
}`

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
  private pointVao?: WebGLVertexArrayObject
  private levelVao?: WebGLVertexArrayObject
  private resolveVao?: WebGLVertexArrayObject
  private pointPos?: WebGLBuffer
  private pointAttr?: WebGLBuffer
  private levelPos?: WebGLBuffer
  private levelColor?: WebGLBuffer
  private palette?: WebGLTexture
  private fbo?: WebGLFramebuffer
  private colorSum?: WebGLTexture
  private density?: WebGLTexture
  private fboSize: [number, number] = [0, 0]
  private heatDrawn = false

  private points?: CityPoints
  /** The heat pyramid, built off the main thread (workers/treeDotsIndex.worker.ts); empty until it arrives. */
  private levels: { cell: number; count: number; chunks: Pick<PickGrid, 'cell' | 'cols' | 'rows' | 'start'> }[] = []
  /** The points' draw chunks: the points are stored in chunk order. */
  private pointChunks?: PickGrid
  /** First item of each level in the level buffers. */
  private levelOffsets: number[] = []
  private levelPositions?: Float32Array
  private levelColorBytes?: Uint8Array
  private uploadLevels = false
  private uploadLevelColors = false
  private indexer?: Worker
  private indexSeq = 0
  private lastBuildId = 0
  private lastColorId = 0
  private grid?: PickGrid
  private indexByRowid = new Int32Array()
  private uploadPoints = false
  private uploadColors = false

  private generation = 0
  private syncing: Promise<void> | null = null
  private syncAgain = false
  /** Last frame's camera, for picking exactly what was drawn. */
  private frame?: { matrix: Float64Array; cx: number; cy: number; width: number; height: number; camdist: number; radius: [number, number] }

  constructor(private source: TreeDotsSource, private onLoaded?: (count: number) => void) {}

  /** WebGL2 with a renderable half-float target: the heat accumulates in one. */
  static supported(map: MapLibreMap): boolean {
    const gl = (map as unknown as { painter?: { context?: { gl?: unknown } } }).painter?.context?.gl
    if (typeof WebGL2RenderingContext === 'undefined' || !(gl instanceof WebGL2RenderingContext)) return false
    return !!(gl.getExtension('EXT_color_buffer_float') || gl.getExtension('EXT_color_buffer_half_float'))
  }

  /** Trees in the drawn set; 0 until the first set arrives. */
  get count(): number { return this.points?.count ?? 0 }
  get loaded(): boolean { return !!this.points }
  get rowsKey(): string | undefined { return this.points?.rowsKey }

  onAdd(map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map = map
    this.gl = gl as GL2
    this.initialize()
    map.on('webglcontextrestored', this.restore)
    if (this.points) { this.uploadPoints = true; this.uploadColors = true }
    if (this.levelPositions) { this.uploadLevels = true; this.uploadLevelColors = true }
  }

  onRemove(_map: MapLibreMap, gl: WebGLRenderingContext | WebGL2RenderingContext) {
    this.map?.off('webglcontextrestored', this.restore)
    this.indexer?.terminate()
    this.indexer = undefined
    this.release(gl as GL2)
    this.map = undefined
    this.gl = undefined
  }

  private restore = () => {
    if (!this.gl) return
    this.initialize()
    this.uploadPoints = true
    this.uploadColors = true
    this.uploadLevels = !!this.levelPositions
    this.uploadLevelColors = !!this.levelColorBytes
    this.map?.triggerRepaint()
  }

  private release(gl: GL2) {
    for (const p of [this.heatProgram, this.resolveProgram, this.circleProgram]) if (p) gl.deleteProgram(p)
    for (const v of [this.pointVao, this.levelVao, this.resolveVao]) if (v) gl.deleteVertexArray(v)
    for (const b of [this.pointPos, this.pointAttr, this.levelPos, this.levelColor]) if (b) gl.deleteBuffer(b)
    for (const t of [this.palette, this.colorSum, this.density]) if (t) gl.deleteTexture(t)
    if (this.fbo) gl.deleteFramebuffer(this.fbo)
    this.fbo = this.colorSum = this.density = undefined
    this.fboSize = [0, 0]
  }

  private initialize() {
    const gl = this.gl!
    if (!gl.getExtension('EXT_color_buffer_float')) gl.getExtension('EXT_color_buffer_half_float')
    this.heatProgram = compile(gl, HEAT_VS, HEAT_FS, 'Heat')
    this.resolveProgram = compile(gl, RESOLVE_VS, RESOLVE_FS, 'Heat resolve')
    this.circleProgram = compile(gl, CIRCLE_VS, CIRCLE_FS, 'Circle')
    this.heatU = uniforms(gl, this.heatProgram, ['u_matrix', 'u_center', 'u_size', 'u_camdist', 'u_intensity'])
    this.resolveU = uniforms(gl, this.resolveProgram, ['u_color', 'u_density', 'u_opacity', 'u_alpha', 'u_saturation'])
    this.circleU = uniforms(gl, this.circleProgram, ['u_matrix', 'u_center', 'u_radius', 'u_dpr', 'u_camdist', 'u_palette', 'u_opacity'])

    this.pointPos = gl.createBuffer()!
    this.pointAttr = gl.createBuffer()!
    this.levelPos = gl.createBuffer()!
    this.levelColor = gl.createBuffer()!

    this.pointVao = gl.createVertexArray()!
    gl.bindVertexArray(this.pointVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pointPos)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 8, 0)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.pointAttr)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 2, gl.UNSIGNED_BYTE, false, 2, 0)

    this.levelVao = gl.createVertexArray()!
    gl.bindVertexArray(this.levelVao)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.levelPos)
    gl.enableVertexAttribArray(0)
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 12, 0)
    gl.enableVertexAttribArray(1)
    gl.vertexAttribPointer(1, 1, gl.FLOAT, false, 12, 8)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.levelColor)
    gl.enableVertexAttribArray(2)
    gl.vertexAttribPointer(2, 4, gl.UNSIGNED_BYTE, true, 4, 0)

    this.resolveVao = gl.createVertexArray()!
    gl.bindVertexArray(null)
    gl.bindBuffer(gl.ARRAY_BUFFER, null)

    this.palette = gl.createTexture()!
    gl.bindTexture(gl.TEXTURE_2D, this.palette)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
    gl.bindTexture(gl.TEXTURE_2D, null)
  }

  // --- Data ---

  /** Drop the drawn set at once (a city switch): nothing of the old city may stay on screen. */
  clear() {
    this.generation++
    this.points = undefined
    this.levels = []
    this.levelPositions = this.levelColorBytes = undefined
    this.lastBuildId = this.lastColorId = ++this.indexSeq
    this.grid = undefined
    this.frame = undefined
    this.map?.triggerRepaint()
  }

  /**
   * Bring the drawn set up to the worker's query and colours. With the same
   * rows only the colours are read and swapped in; anything else re-reads
   * the points. The current set stays drawn until its replacement is ready.
   */
  sync(): Promise<void> {
    if (this.syncing) { this.syncAgain = true; return this.syncing }
    this.syncing = (async () => {
      try {
        do {
          this.syncAgain = false
          await this.syncOnce()
        } while (this.syncAgain)
      } catch (error) {
        // The drawn set stays; the next query or colour change retries.
        console.warn('[TreeDots] refresh failed', error)
      } finally {
        this.syncing = null
      }
    })()
    return this.syncing
  }

  private async syncOnce() {
    const generation = this.generation
    const t0 = performance.now()
    if (this.points) {
      const colors = await this.source.getCityPointColors(this.points.rowsKey)
      if (generation !== this.generation) return
      if (colors) {
        if (colors.colorKey !== this.points.colorKey) this.applyColors(colors)
        console.info('[Perf] map:dots:recolor', { ms: Math.round(performance.now() - t0) })
        return
      }
    }
    const points = await this.source.getCityPoints()
    if (generation !== this.generation || !points) return
    this.setPoints(points)
    console.info('[Perf] map:dots:points', { rows: points.count, ms: Math.round(performance.now() - t0) })
  }

  private index(request: DotsIndexRequest, transfer: Transferable[]) {
    if (!this.indexer) {
      this.indexer = new TreeDotsIndexWorker({ name: 'tree-dots-index' })
      this.indexer.onmessage = (event: MessageEvent<DotsIndexResult>) => this.onIndexed(event.data)
    }
    this.indexer.postMessage(request, transfer)
  }

  private onIndexed(result: DotsIndexResult) {
    if (result.positions && result.counts && result.cells) {
      if (result.id !== this.lastBuildId) return
      this.levels = result.cells.map((cell, i) => ({ cell, count: result.counts![i], chunks: result.chunks![i] }))
      this.levelOffsets = []
      let offset = 0
      for (const level of this.levels) { this.levelOffsets.push(offset); offset += level.count }
      this.levelPositions = result.positions
      this.uploadLevels = true
    }
    if (result.id !== this.lastColorId) return
    this.levelColorBytes = result.colors
    this.uploadLevelColors = true
    this.map?.triggerRepaint()
  }

  private setPoints(unordered: CityPoints) {
    const t0 = performance.now()
    // Store the points in coarse-cell order, so a frame draws only the
    // ranges of the cells in view (visibleRanges).
    const chunks = buildPickGrid(unordered.x, unordered.y, unordered.count, DRAW_CHUNK_MAX_CELLS, DRAW_CHUNK_MIN_M)
    const order = chunks.order
    const points: CityPoints = {
      ...unordered,
      x: Float32Array.from(order, (i) => unordered.x[i]),
      y: Float32Array.from(order, (i) => unordered.y[i]),
      dbh: Uint8Array.from(order, (i) => unordered.dbh[i]),
      color: Uint8Array.from(order, (i) => unordered.color[i]),
      rowid: Uint32Array.from(order, (i) => unordered.rowid[i]),
    }
    this.pointChunks = chunks
    this.points = points
    // The heat waits for its pyramid; the circles and picking need only the points.
    this.levels = []
    this.levelPositions = this.levelColorBytes = undefined
    this.lastBuildId = this.lastColorId = ++this.indexSeq
    const x = points.x.slice(), y = points.y.slice(), color = points.color.slice()
    this.index({ kind: 'build', id: this.lastBuildId, x, y, count: points.count, color, palette: points.palette }, [x.buffer, y.buffer, color.buffer])
    this.grid = buildPickGrid(points.x, points.y, points.count)
    let maxRowid = 0
    for (let i = 0; i < points.count; i++) if (points.rowid[i] > maxRowid) maxRowid = points.rowid[i]
    this.indexByRowid = new Int32Array(points.count ? maxRowid + 1 : 0).fill(-1)
    for (let i = 0; i < points.count; i++) this.indexByRowid[points.rowid[i]] = i
    this.uploadPoints = true
    this.uploadColors = true
    console.info('[Perf] map:dots:index', { rows: points.count, ms: Math.round(performance.now() - t0) })
    this.map?.triggerRepaint()
    this.onLoaded?.(points.count)
  }

  private applyColors(colors: CityPointColors) {
    const points = this.points!
    const next = new Uint8Array(points.count)
    for (let k = 0; k < colors.rowid.length; k++) {
      const i = this.indexByRowid[colors.rowid[k]]
      if (i !== undefined && i >= 0) next[i] = colors.color[k]
    }
    this.points = { ...points, color: next, palette: colors.palette, colorKey: colors.colorKey }
    this.uploadColors = true
    this.lastColorId = ++this.indexSeq
    const color = next.slice()
    this.index({ kind: 'recolor', id: this.lastColorId, color, palette: colors.palette }, [color.buffer])
    this.map?.triggerRepaint()
  }

  private upload(gl: GL2) {
    const points = this.points!
    if (this.uploadPoints) {
      const pos = new Float32Array(points.count * 2)
      for (let i = 0; i < points.count; i++) { pos[i * 2] = points.x[i]; pos[i * 2 + 1] = points.y[i] }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.pointPos!)
      gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW)
      this.uploadPoints = false
    }
    if (this.uploadColors) {
      const attr = new Uint8Array(points.count * 2)
      for (let i = 0; i < points.count; i++) { attr[i * 2] = points.color[i]; attr[i * 2 + 1] = points.dbh[i] }
      gl.bindBuffer(gl.ARRAY_BUFFER, this.pointAttr!)
      gl.bufferData(gl.ARRAY_BUFFER, attr, gl.STATIC_DRAW)
      const pal = new Uint8Array(256 * 4)
      points.palette.forEach((hex, i) => { const [r, g, b] = hexRgb(hex); pal.set([r, g, b, 255], i * 4) })
      gl.bindTexture(gl.TEXTURE_2D, this.palette!)
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 4)
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false)
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, pal)
      gl.bindTexture(gl.TEXTURE_2D, null)
      this.uploadColors = false
    }
    if (this.uploadLevels && this.levelPositions) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.levelPos!)
      gl.bufferData(gl.ARRAY_BUFFER, this.levelPositions, gl.STATIC_DRAW)
      this.uploadLevels = false
    }
    if (this.uploadLevelColors && this.levelColorBytes) {
      gl.bindBuffer(gl.ARRAY_BUFFER, this.levelColor!)
      gl.bufferData(gl.ARRAY_BUFFER, this.levelColorBytes, gl.STATIC_DRAW)
      this.uploadLevelColors = false
    }
    gl.bindBuffer(gl.ARRAY_BUFFER, null)
  }

  private needsUpload(): boolean {
    return this.uploadPoints || this.uploadColors || this.uploadLevels || this.uploadLevelColors
  }

  // --- Drawing ---

  /**
   * The view's bounding box in local metres, padded by `padPx` CSS pixels:
   * what a draw needs to cover. A pitched view's box reaches the horizon.
   */
  private viewBox(padPx: number): [number, number, number, number] {
    const map = this.map!
    const points = this.points!
    const b = map.getBounds()
    const [x0, y0] = lngLatTo3857(b.getWest(), b.getSouth())
    const [x1, y1] = lngLatTo3857(b.getEast(), b.getNorth())
    const pad = padPx * metresPerPixel(map.getZoom())
    return [x0 - points.originX - pad, y0 - points.originY - pad, x1 - points.originX + pad, y1 - points.originY + pad]
  }

  private drawRanges(gl: GL2, ranges: number[], offset = 0) {
    for (let k = 0; k < ranges.length; k += 2) gl.drawArrays(gl.POINTS, offset + ranges[k], ranges[k + 1] - ranges[k])
  }

  private camera(matrix: ArrayLike<number>) {
    const map = this.map!
    const points = this.points!
    const centre = map.getCenter()
    const [cx3857, cy3857] = lngLatTo3857(centre.lng, centre.lat)
    // The centre as the shader sees it: float32 metres from the origin.
    const cx = Math.fround(cx3857 - points.originX)
    const cy = Math.fround(cy3857 - points.originY)
    const rel = relativeMatrix(matrix, points.originX + cx, points.originY + cy)
    const canvas = map.getCanvas()
    return {
      rel, cx, cy,
      zoom: map.getZoom(),
      camdist: map.transform.cameraToCenterDistance,
      width: canvas.clientWidth,
      height: canvas.clientHeight,
      dpr: canvas.width / Math.max(1, canvas.clientWidth),
    }
  }

  prerender(gl: WebGLRenderingContext | WebGL2RenderingContext, matrix: Parameters<CustomLayerInterface['render']>[1]) {
    this.heatDrawn = false
    if (!this.map || !this.points?.count || !this.heatProgram) return
    const g = gl as GL2
    if (this.needsUpload()) this.upload(g)
    const zoom = this.map.getZoom()
    if (zoom < HEAT_MIN_ZOOM || zoom >= HEAT_MAX_ZOOM || heatOpacity(zoom) <= 0) return
    // The pyramid and its colours arrive from the indexer; until both have, there is no heat.
    if (!this.levels.length || !this.levelColorBytes || this.uploadLevels) return
    const cam = this.camera(matrix as ArrayLike<number>)
    const w = Math.max(1, Math.ceil(g.drawingBufferWidth / HEAT_SCALE))
    const h = Math.max(1, Math.ceil(g.drawingBufferHeight / HEAT_SCALE))
    this.ensureTarget(g, w, h)

    const radiusPx = heatRadius(zoom)
    const levelIndex = levelForKernel(this.levels, radiusPx * metresPerPixel(zoom))

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
    g.uniformMatrix4fv(this.heatU.u_matrix, false, new Float32Array(cam.rel))
    g.uniform2f(this.heatU.u_center, cam.cx, cam.cy)
    g.uniform1f(this.heatU.u_size, (2 * radiusPx * cam.dpr) / HEAT_SCALE)
    g.uniform1f(this.heatU.u_camdist, cam.camdist)
    g.uniform1f(this.heatU.u_intensity, heatIntensity(zoom))
    g.bindVertexArray(this.levelVao!)
    const box = this.viewBox(radiusPx * 1.5)
    this.drawRanges(g, visibleRanges(this.levels[levelIndex].chunks, ...box), this.levelOffsets[levelIndex])
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
    if (!this.map || !this.points?.count || !this.circleProgram) return
    const g = gl as GL2
    if (this.needsUpload()) this.upload(g)
    const zoom = this.map.getZoom()
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
    const cam = this.camera(matrix as ArrayLike<number>)
    const radius = circleRadiusRange(zoom)
    this.frame = { matrix: cam.rel, cx: cam.cx, cy: cam.cy, width: cam.width, height: cam.height, camdist: cam.camdist, radius }
    if (opacity > 0) {
      g.useProgram(this.circleProgram)
      g.uniformMatrix4fv(this.circleU.u_matrix, false, new Float32Array(cam.rel))
      g.uniform2f(this.circleU.u_center, cam.cx, cam.cy)
      g.uniform2f(this.circleU.u_radius, radius[0], radius[1])
      g.uniform1f(this.circleU.u_dpr, cam.dpr)
      g.uniform1f(this.circleU.u_camdist, cam.camdist)
      g.uniform1f(this.circleU.u_opacity, opacity)
      g.activeTexture(g.TEXTURE0)
      g.bindTexture(g.TEXTURE_2D, this.palette!)
      g.uniform1i(this.circleU.u_palette, 0)
      g.bindVertexArray(this.pointVao!)
      if (this.pointChunks) this.drawRanges(g, visibleRanges(this.pointChunks, ...this.viewBox(radius[1] + 2)))
      g.bindTexture(g.TEXTURE_2D, null)
    }
    g.bindVertexArray(null)
  }

  // --- Picking ---

  /** True while the circles are drawn, the zooms a tap selects a tree at. */
  pickable(): boolean {
    const zoom = this.map?.getZoom() ?? 0
    return !!this.points?.count && zoom >= CIRCLE_MIN_ZOOM && zoom < CIRCLE_MAX_ZOOM
  }

  /**
   * Up to `max` circles drawn last frame inside the canvas, as CSS-pixel
   * centres: diagnostics and tests, since a custom layer has no
   * queryRenderedFeatures.
   */
  visibleTrees(max = 200): { x: number; y: number; rowid: number }[] {
    const frame = this.frame
    const points = this.points
    if (!this.map || !frame || !points || !this.pickable()) return []
    const out: { x: number; y: number; rowid: number }[] = []
    const seen = new Set<number>()
    const m = frame.matrix
    const [x0, y0, x1, y1] = this.viewBox(0)
    const reach = Math.max(x1 - x0, y1 - y0) / 2
    // From the centre outward, so a short list is the middle of the view.
    for (let r = reach / 16; out.length < max; r *= 2) {
      pointsNear(this.grid!, (x0 + x1) / 2, (y0 + y1) / 2, r, (i) => {
        if (out.length >= max || seen.has(i)) return
        seen.add(i)
        const dx = points.x[i] - frame.cx
        const dy = points.y[i] - frame.cy
        const cw = m[3] * dx + m[7] * dy + m[15]
        if (cw <= 0) return
        const x = ((m[0] * dx + m[4] * dy + m[12]) / cw + 1) * 0.5 * frame.width
        const y = (1 - (m[1] * dx + m[5] * dy + m[13]) / cw) * 0.5 * frame.height
        if (x >= 0 && y >= 0 && x <= frame.width && y <= frame.height) out.push({ x, y, rowid: points.rowid[i] })
      })
      if (r >= reach) break
    }
    return out
  }

  /** The circle drawn under a CSS-pixel point, nearest centre first, as last frame drew it. */
  pick(point: { x: number; y: number }, tolerancePx = 2): TreeDotsPick | undefined {
    const frame = this.frame
    const points = this.points
    if (!this.map || !frame || !points || !this.grid || !this.pickable()) return undefined
    const at = this.map.unproject([point.x, point.y])
    const [qx3857, qy3857] = lngLatTo3857(at.lng, at.lat)
    const qx = qx3857 - points.originX
    const qy = qy3857 - points.originY
    // Generous in metres (a pitched view stretches the far side); the test below is in pixels.
    const reach = (frame.radius[1] + tolerancePx) * metresPerPixel(this.map.getZoom()) * 4
    const m = frame.matrix
    let best = -1
    let bestDistance = Infinity
    pointsNear(this.grid, qx, qy, reach, (i) => {
      const dx = points.x[i] - frame.cx
      const dy = points.y[i] - frame.cy
      const cw = m[3] * dx + m[7] * dy + m[15]
      if (cw <= 0) return
      const sx = ((m[0] * dx + m[4] * dy + m[12]) / cw + 1) * 0.5 * frame.width
      const sy = (1 - (m[1] * dx + m[5] * dy + m[13]) / cw) * 0.5 * frame.height
      const distance = Math.hypot(sx - point.x, sy - point.y)
      const r = (circleRadius(points.dbh[i], frame.radius) + 0.5) * (frame.camdist / cw)
      if (distance <= r + tolerancePx && distance < bestDistance) { best = i; bestDistance = distance }
    })
    if (best < 0) return undefined
    const [lng, lat] = lngLatFrom3857(points.x[best] + points.originX, points.y[best] + points.originY)
    return { rowid: points.rowid[best], lng, lat }
  }
}
