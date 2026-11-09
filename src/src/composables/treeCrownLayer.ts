import type { CustomLayerInterface, Map as TreeMap } from 'maplibre-gl'
import { createTreeSpriteAtlas } from './treeSpriteAtlas'
import {
  CROWN_DISTANCE_FULL, CROWN_DISTANCE_MAX, SPRITE_LIMIT_KEY, CROWN_ZOOM_START,
  crownZoomOpacity, mercatorPoint, metersPerMercatorUnit,
  spriteOpacity, SPRITE_CROWN_ZOOM_START, MIN_SPRITE_PX, REACH_FADE,
  type CrownCamera, type TreeRenderView,
} from '../lib/treeCrowns'
import { CROWN_STRIDE as STRIDE, emptyCrownBatch, type CrownBatch, type CrownBatchOptions, type SpriteCellGeometry } from '../lib/crownBatch'
import { FOOTPRINT_PAD, insidePolygon, viewFootprint, type Point } from '../lib/viewFootprint'
import { lngLatFrom3857, lngLatTo3857, MERCATOR_WORLD_M } from '../lib/treeDots'
import { crownVisibleDistance, initialSpriteBudget, isSoftwareRenderer, nextSpriteBudget, type BatchSample, type SpriteBudget } from '../lib/spriteBudget'

type GL = WebGLRenderingContext | WebGL2RenderingContext
/** A 60 Hz frame; what an upload's frame takes beyond it is the upload's cost. */
const FRAME_MS = 1000 / 60
type LoadCrowns = (camera: TreeRenderView, options: CrownBatchOptions) => Promise<CrownBatch>
/** What a pick or the intro's readiness check learns about a drawn tree. */
export interface DrawnTree { id: string; lng: number; lat: number; width: number }
type ViewBounds = NonNullable<TreeRenderView['bounds']>

/** The lng/lat box around an EPSG:3857 polygon: the query's coarse prefilter. */
function polygonBounds(polygon: Point[]): ViewBounds {
  const corners = polygon.map(([x, y]) => lngLatFrom3857(x, y))
  const lngs = corners.map(c => c[0]), lats = corners.map(c => c[1])
  return { west: Math.min(...lngs), east: Math.max(...lngs), south: Math.max(-85.05112878, Math.min(...lats)), north: Math.min(85.05112878, Math.max(...lats)) }
}

function spriteLimitOverride(): number | undefined {
  try {
    const value = Number(localStorage.getItem(SPRITE_LIMIT_KEY))
    return Number.isInteger(value) && value > 0 ? value : undefined
  } catch { return undefined }
}

/** For the debug radar: the camera, the view, and what is fetched around them, in EPSG:3857 metres. */
export interface SpriteRadar {
  eye: Point
  bearing: number
  /** The ground the screen shows now. */
  view: Point[]
  /** The installed batch's fetch polygon, and the camera it was queried from. */
  fetched: Point[] | null
  queryEye: Point | null
  /** EPSG:3857 metres; Infinity when the batch was not cut by the cap. */
  reach: number
  /** EPSG:3857 metres; how far a typical crown stays MIN_SPRITE_PX wide, the reach the budget aims for. */
  target: number
  /** The fetch polygon of the query in flight. */
  pending: Point[] | null
  /** A sample of the drawn trees, x/y interleaved. */
  trees: Float64Array
}

/** One bounded batch of upright sprites or ground-plane rings. Camera changes only uniforms;
 * no sprite regeneration, symbol placement, tile rebuilds or per-frame SQL.
 * Uses the typed camera accessor in our pinned MapLibre 4.x transform.
 */

export class TreeCrownLayer implements CustomLayerInterface {
  get id() { return this.simplified ? 'trees-crown' : 'trees-icon' }
  readonly type = 'custom' as const
  readonly renderingMode = '2d' as const
  private map?: TreeMap
  private gl?: GL
  private program?: WebGLProgram
  private buffer?: WebGLBuffer
  private attributes: number[] = []
  private uniforms: Record<string, WebGLUniformLocation | null> = {}
  private timer?: ReturnType<typeof setTimeout>
  private inFlight = false
  private dirty = true
  private generation = 0
  private count = 0
  private origin: [number, number] = [0, 0]
  private data = new Float32Array()
  private upload = false
  private appearedAt = 0
  private budgetCamera = [0, 0, 0]
  private budgetDistance = CROWN_DISTANCE_MAX
  /** Metres from the query camera of the farthest sprite loaded when the batch hit its cap; Infinity when nothing was cut. */
  private reach = Infinity
  /** Where the camera stood when the installed batch was queried; a capped batch is a disc around it. */
  private queryCamera?: CrownCamera
  /** The padded view footprint the installed batch was fetched for (EPSG:3857 metres). */
  private loadedFootprint?: Point[]
  private pendingFootprint?: Point[]
  /** A fixed desktop sprite budget from localStorage (SPRITE_LIMIT_KEY), for sweeps; otherwise it adjusts itself (lib/spriteBudget). */
  private readonly fixedLimit = spriteLimitOverride()
  private budget: SpriteBudget = initialSpriteBudget(false)
  private get spriteLimit() { return this.fixedLimit ?? this.budget.limit }
  /** The installed batch, waiting for its upload time before it adjusts the budget. */
  private sample?: Omit<BatchSample, 'uploadMs'>
  private uploadedAt?: number
  private debug = { queries: 0, discarded: 0, lastQueryMs: 0, lastSqlMs: 0, lastBuildMs: 0, lastBatch: 0, lastUploadMs: 0 }
  private sprites?: ReturnType<typeof createTreeSpriteAtlas>
  private textures: WebGLTexture[] = []
  /** The installed batch's drawn trees, in draw order; `data` holds their vertices. */
  private drawn: CrownBatch = emptyCrownBatch()
  private drawnTrees?: DrawnTree[]
  /** The footprint of an uncapped batch: every tree in it is loaded, so views inside it need no query. */
  private coverage?: Point[]
  private coverageBearing = 0
  private get minZoom() { return this.simplified ? CROWN_ZOOM_START : SPRITE_CROWN_ZOOM_START }

  constructor(private load: LoadCrowns, private simplified: boolean) {}

  /**
   * Where the desktop sprites stand in for the dots: inside `polygon` (the fetched footprint in
   * EPSG:3857 metres; null when no batch is installed) and within `reach` metres of `eye`, the camera
   * the batch was queried from (not the camera now: a moving camera would carry the disc past the
   * trees loaded). Null below the sprite zooms.
   */
  spriteCoverage(): { polygon: Point[] | null; reach: number; eye: CrownCamera | null } | null {
    if (this.simplified || !this.map || this.map.getZoom() <= this.minZoom) return null
    const installed = this.count > 0 && !!this.loadedFootprint
    return { polygon: installed ? this.loadedFootprint! : null, reach: this.reach, eye: installed ? this.queryCamera ?? null : null }
  }

  radar(): SpriteRadar | null {
    if (this.simplified || !this.map) return null
    const { lngLat } = this.map.transform.getCameraPosition()
    const toPoint = (c?: CrownCamera): Point | null => c ? lngLatTo3857(c.lng, c.lat) as Point : null
    const scale = 1 / Math.cos(lngLat.lat * Math.PI / 180)
    const trees = this.count ? this.drawn.id.length : 0
    const step = Math.max(1, Math.ceil(trees / 3000))
    const sample = new Float64Array(Math.ceil(trees / step) * 2)
    for (let i = 0, k = 0; i < trees; i += step) {
      const offset = i * STRIDE * 6
      sample[k++] = (this.origin[0] + this.data[offset] - 0.5) * MERCATOR_WORLD_M
      sample[k++] = (0.5 - this.origin[1] - this.data[offset + 1]) * MERCATOR_WORLD_M
    }
    return {
      eye: lngLatTo3857(lngLat.lng, lngLat.lat) as Point, bearing: this.map.getBearing(),
      view: this.footprint(0), fetched: this.count ? this.loadedFootprint ?? null : null,
      queryEye: this.count ? toPoint(this.queryCamera) : null, reach: this.reach * scale, target: this.target() * scale,
      pending: this.inFlight ? this.pendingFootprint ?? null : null, trees: sample,
    }
  }

  /** A query is waiting on its timer or running. */
  busy(): boolean {
    return !!this.timer || this.inFlight
  }

  /** For the debug readout. */
  stats() {
    return {
      drawn: this.drawn.id.length, cap: this.spriteLimit, auto: this.fixedLimit === undefined, ceiling: this.budget.ceiling,
      reach: this.reach, target: this.map ? this.target() : 0, inFlight: this.inFlight, ...this.debug,
    }
  }

  /** Metres at which a typical crown is MIN_SPRITE_PX wide in this view. */
  private target(): number {
    return crownVisibleDistance(this.map!.transform.cameraToCenterDistance)
  }

  private camera(): CrownCamera {
    const { lngLat, altitude } = this.map!.transform.getCameraPosition()
    return { lng: lngLat.wrap().lng, lat: lngLat.lat, altitude }
  }

  /** Called before a source revision/filter/city refresh: stale crowns vanish immediately. */
  invalidate = () => {
    this.generation++
    this.count = 0
    this.coverage = undefined
    this.schedule()
    this.map?.triggerRepaint()
  }

  /**
   * The ground the screen shows, as an EPSG:3857 polygon, padded by `pad` of its depth behind and to
   * the sides. The top corners stop just under the horizon, as MapLibre's own getBounds does.
   */
  private footprint(pad: number): Point[] {
    const map = this.map!, transform = map.transform
    const width = transform.width, height = transform.height
    const top = Math.max(0, height / 2 - transform.getHorizon())
    const ground = ([[0, height], [width, height], [width, top], [0, top]] as Point[]).map(([x, y]) => {
      const { lng, lat } = map.unproject([x, y])
      return lngLatTo3857(lng, lat) as Point
    })
    const { lngLat } = transform.getCameraPosition()
    return viewFootprint(lngLatTo3857(lngLat.lng, lngLat.lat) as Point, map.getBearing(), ground, pad, 1 / Math.cos(lngLat.lat * Math.PI / 180))
  }

  private schedule = () => {
    this.dirty = true
    if (this.map && this.map.getZoom() <= this.minZoom) {
      this.count = 0
      this.coverage = undefined
    }
    if (this.timer || this.inFlight || !this.map || this.map.getZoom() <= this.minZoom) return
    this.timer = setTimeout(() => { this.timer = undefined; void this.refresh() }, 250)
  }

  private async refresh() {
    if (!this.map || this.map.getZoom() <= this.minZoom) return
    // Read the city snapshot directly; far-away vector tiles must not hold up
    // desktop sprites in a pitched viewport. The worker gates city readiness.
    const camera: TreeRenderView = this.camera()
    if (this.simplified && camera.altitude >= CROWN_DISTANCE_MAX) return
    this.dirty = false
    if (!this.simplified) {
      // Reuse only complete (uncapped) batches whose footprint still holds the whole view, at the
      // bearing they were sorted for.
      const coverage = this.coverage
      if (coverage && this.coverageBearing === this.map.getBearing() && this.footprint(0).every(p => insidePolygon(coverage, p))) return
      // Fetch the view's own footprint, padded behind and to the sides so a reverse or a spin
      // starts from trees already loaded; nearest first, up to the budget.
      camera.footprint = this.footprint(FOOTPRINT_PAD)
      camera.bounds = polygonBounds(camera.footprint)
      camera.spriteLimit = this.spriteLimit
      this.pendingFootprint = camera.footprint
    }
    this.inFlight = true
    const generation = this.generation
    try {
      const started = performance.now()
      // The batch is sorted back to front for this bearing; a view at another needs a new batch.
      const bearing = this.map.getBearing()
      const batch = await this.load(camera, { simplified: this.simplified, bearing, cells: this.cellGeometry() })
      this.debug.queries++
      this.debug.lastQueryMs = performance.now() - started
      this.debug.lastBatch = batch.rows
      this.debug.lastSqlMs = batch.timing.sqlMs
      this.debug.lastBuildMs = batch.timing.buildMs
      if (!this.map || generation !== this.generation) return
      if (this.map.getZoom() <= this.minZoom || (this.simplified && this.camera().altitude >= CROWN_DISTANCE_MAX)) return
      const center = this.map.getCenter()
      if (camera.footprint && !insidePolygon(camera.footprint, lngLatTo3857(center.lng, center.lat) as Point)) {
        // Keep the last useful batch while catching up; never replace it with
        // a late snapshot of a view the user has already turned or moved away from.
        this.debug.discarded++
        this.dirty = true
        return
      }
      this.setBatch(batch, camera)
      this.loadedFootprint = camera.footprint
      const limit = camera.spriteLimit ?? this.spriteLimit, capped = batch.rows >= limit
      this.coverage = camera.footprint && !capped ? camera.footprint : undefined
      if (!this.simplified) this.sample = { limit, capped, reach: batch.reach, target: this.target(), buildMs: batch.timing.buildMs }
      this.coverageBearing = bearing
    } catch (error) {
      if (this.map && generation === this.generation) {
        // A transient query error must not blank an otherwise usable batch.
        console.warn('[TreeCrowns] nearby crowns unavailable', error)
      }
    } finally {
      this.inFlight = false
      this.pendingFootprint = undefined
      if (this.dirty) this.schedule()
    }
  }

  /** The atlas cells' geometry, for the worker's vertex fill. */
  private cellGeometry(): Record<string, SpriteCellGeometry> | undefined {
    if (!this.sprites) return undefined
    const cells: Record<string, SpriteCellGeometry> = {}
    for (const [form, { left, right, top, bottom, crownFraction, heightFraction, base }] of this.sprites.cells) {
      cells[form] = { left, right, top, bottom, crownFraction, heightFraction, base }
    }
    return cells
  }

  /** Install a batch the worker sorted and filled: one buffer upload on the next frame. */
  private setBatch(batch: CrownBatch, camera: CrownCamera) {
    const wasEmpty = this.count === 0
    this.origin = batch.origin
    this.budgetCamera = [0, 0, camera.altitude / metersPerMercatorUnit(camera.lat)]
    this.budgetDistance = batch.budgetDistance
    this.reach = batch.reach
    this.queryCamera = { lng: camera.lng, lat: camera.lat, altitude: camera.altitude }
    this.drawn = batch
    this.drawnTrees = undefined
    this.data = batch.vertices
    this.count = batch.vertices.length / STRIDE
    this.upload = true
    if (wasEmpty) this.appearedAt = performance.now()
    this.map?.triggerRepaint()
  }

  /**
   * After a batch's upload, step the sprite budget toward the reach a typical crown is worth drawing
   * at, within what the upload and the worker's build cost. A capped batch that fell short is
   * fetched again at the new limit.
   */
  private adjustBudget(uploadMs: number) {
    const sample = this.sample
    this.sample = undefined
    if (!sample || this.fixedLimit !== undefined) return
    const before = this.budget.limit
    this.budget = nextSpriteBudget(this.budget, { ...sample, uploadMs })
    if (this.budget.limit > before && sample.capped) {
      this.coverage = undefined
      this.schedule()
    }
  }

  /** Match the visible, scaled silhouette rather than the old marker bounds. */
  pick(point: { x: number; y: number }): DrawnTree | undefined {
    if (this.simplified || !this.map || !this.count) return
    const map = this.map, zoom = map.getZoom()
    if (zoom <= this.minZoom) return
    const matrix = map.transform.customLayerMatrix()
    const width = map.getCanvas().clientWidth, height = map.getCanvas().clientHeight
    // Reach is measured from the query camera, as the shader does.
    const meters = metersPerMercatorUnit(this.queryCamera?.lat ?? 0)
    const bearing = map.getBearing() * Math.PI / 180
    const rightClip = matrix[0] * Math.cos(bearing) + matrix[4] * Math.sin(bearing)
    const { id, lng, lat, width: widths, category } = this.drawn
    for (let i = id.length - 1; i >= 0; i--) {
      const cell = this.sprites!.cells.get(category[i]) ?? this.sprites!.cells.get('default')!
      const offset = i * STRIDE * 6
      const x = this.data[offset] + this.origin[0], y = this.data[offset + 1] + this.origin[1]
      const w = matrix[3] * x + matrix[7] * y + matrix[15]
      if (w <= 0) continue
      const sx = ((matrix[0] * x + matrix[4] * y + matrix[12]) / w + 1) * width / 2
      const sy = (1 - (matrix[1] * x + matrix[5] * y + matrix[13]) / w) * height / 2
      const distance = Math.hypot(this.data[offset], this.data[offset + 1], this.budgetCamera[2]) * meters
      if (distance >= this.reach) continue
      const size = Math.max(Math.abs(rightClip * this.data[offset + 4] / cell.crownFraction) / w * width, MIN_SPRITE_PX)
      const tall = size * this.data[offset + 14]
      const u = (point.x - sx) / size + 0.5, v = (point.y - sy) / tall + cell.base
      if (u < 0 || u >= 1 || v < 0 || v >= 1) continue
      if (cell.alpha[Math.floor(v * cell.size) * cell.size + Math.floor(u * cell.size)] > 32) return { id: id[i], lng: lng[i], lat: lat[i], width: widths[i] }
    }
  }

  /** Read-only diagnostics/click candidates; custom layers have no MapLibre hit index. */
  get trees(): readonly DrawnTree[] {
    if (!this.count) return []
    const { id, lng, lat, width } = this.drawn
    return this.drawnTrees ??= id.map((treeId, i) => ({ id: treeId, lng: lng[i], lat: lat[i], width: width[i] }))
  }

  onAdd(map: TreeMap, gl: GL) {
    this.map = map
    this.gl = gl
    this.initialize(gl)
    map.on('move', this.schedule)
    map.on('webglcontextrestored', this.restore)
    this.schedule()
  }

  private restore = () => {
    if (this.gl) { this.initialize(this.gl); this.upload = true; this.map?.triggerRepaint() }
  }

  private initialize(gl: GL) {
    if (!this.simplified) this.sprites ??= createTreeSpriteAtlas()
    if (!this.simplified && this.debug.queries === 0) {
      const info = gl.getExtension('WEBGL_debug_renderer_info')
      this.budget = initialSpriteBudget(isSoftwareRenderer(info ? String(gl.getParameter(info.UNMASKED_RENDERER_WEBGL)) : ''))
    }
    const webgl2 = typeof WebGL2RenderingContext !== 'undefined' && gl instanceof WebGL2RenderingContext
    const derivatives = webgl2 || !!gl.getExtension('OES_standard_derivatives')
    const header = webgl2 ? '#version 300 es\n' : ''
    const attribute = webgl2 ? 'in' : 'attribute'
    const vertex = `${header}precision highp float;
      ${attribute} vec2 a_center;
      ${attribute} vec2 a_corner;
      ${attribute} float a_radius;
      ${attribute} vec4 a_color;
      ${!this.simplified ? `${attribute} vec2 a_uv;
      ${attribute} vec4 a_shape;
      uniform vec2 u_viewport;
      uniform vec2 u_right;
      ${webgl2 ? 'out' : 'varying'} vec2 v_uv;` : ''}
      uniform mat4 u_matrix;
      uniform vec3 u_camera;
      uniform vec3 u_budget_camera;
      uniform float u_meters;
      uniform float u_budget_distance;
      ${webgl2 ? 'out' : 'varying'} vec2 v_corner;
      ${webgl2 ? 'out' : 'varying'} vec4 v_color;
      ${webgl2 ? 'out' : 'varying'} float v_fade;
      ${webgl2 ? 'out' : 'varying'} float v_reach;
      uniform float u_reach;
      void main() {
        float distance = length(vec3(a_center, 0.0) - u_camera) * u_meters;
        float budget = length(vec3(a_center, 0.0) - u_budget_camera) * u_meters;
        v_fade = (1.0 - smoothstep(${CROWN_DISTANCE_FULL}.0, ${CROWN_DISTANCE_MAX}.0, distance))
          * (1.0 - smoothstep(u_budget_distance * 0.85, u_budget_distance, budget));
        if (a_radius <= 0.0) v_fade = 0.0;
        // Past the farthest sprite of a capped batch the dots carry the trees. Measured from
        // the query camera (u_budget_camera), so the fade stays on the trees actually loaded.
        v_reach = 1.0 - smoothstep(u_reach * ${REACH_FADE}, u_reach, budget);
        v_corner = a_corner;
        v_color = a_color;
        ${this.simplified ? `gl_Position = v_fade > 0.0
          ? u_matrix * vec4(a_center + a_corner * a_radius, 0.0, 1.0)
          : vec4(2.0, 2.0, 2.0, 1.0);` : `
        vec4 anchor = u_matrix * vec4(a_center, 0.0, 1.0);
        vec4 crownRight = u_matrix * vec4(u_right * a_radius / a_shape.x, 0.0, 0.0);
        // Always metre scale, never narrower than MIN_SPRITE_PX on screen.
        float halfWidth = max(abs(crownRight.x), ${MIN_SPRITE_PX.toFixed(1)} / u_viewport.x * anchor.w);
        // As tall as its measured or predicted height (a_shape.w, clamped on the CPU).
        float halfHeight = halfWidth * u_viewport.x / u_viewport.y * a_shape.w;
        anchor.xy += vec2(a_corner.x * halfWidth, (a_corner.y + 2.0 * a_shape.z - 1.0) * halfHeight);
        gl_Position = anchor.w > 0.0 ? anchor : vec4(2.0, 2.0, 2.0, 1.0);
        v_uv = a_uv;`}
      }`
    const fragment = `${header}${!webgl2 && derivatives ? '#extension GL_OES_standard_derivatives : enable\n' : ''}
      precision highp float;
      ${webgl2 ? 'in' : 'varying'} vec2 v_corner;
      ${webgl2 ? 'in' : 'varying'} vec4 v_color;
      ${webgl2 ? 'in' : 'varying'} float v_fade;
      ${webgl2 ? 'in' : 'varying'} float v_reach;
      uniform float u_opacity;
      ${!this.simplified ? `uniform sampler2D u_distance;
      ${webgl2 ? 'in' : 'varying'} vec2 v_uv;` : ''}
      ${webgl2 ? 'out vec4 fragColor;' : ''}
      void main() {
        ${this.simplified ? `
        float radius = length(v_corner);
        float aa = ${derivatives ? 'max(fwidth(radius), 0.002)' : '0.025'};
        float edge = 1.0 - smoothstep(1.0 - aa, 1.0, radius);
        float ring = smoothstep(1.0 - max(aa * 1.8, 0.025) - aa, 1.0 - max(aa * 1.8, 0.025), radius);
        // A broken outline identifies a model estimate; solid means recorded.
        float dash = v_color.a > 0.5 ? 1.0 : step(0.28, fract((atan(v_corner.y, v_corner.x) + 3.141593) * 12.0 / 6.283186));
        float alpha = edge * (0.015 + ring * dash * 0.65) * v_fade * u_opacity;
        ${webgl2 ? 'fragColor' : 'gl_FragColor'} = vec4(v_color.rgb * alpha, alpha);
        ` : `
        vec3 distance = ${webgl2 ? 'texture' : 'texture2D'}(u_distance, v_uv).rgb;
        // Screen-space derivatives keep the edge about one physical pixel
        // wide, whether the sprite is a small marker or a large nearby tree.
        vec3 aa = ${derivatives ? 'max(fwidth(distance) * 0.5, vec3(0.001))' : 'vec3(0.025)'};
        vec3 coverage = smoothstep(vec3(0.5) - aa, vec3(0.5) + aa, distance);
        vec4 trunk = vec4(vec3(93.0, 64.0, 55.0) / 255.0 * coverage.r, coverage.r);
        vec4 foliage = vec4(v_color.rgb * coverage.g, coverage.g);
        vec4 blooms = vec4(vec3(248.0, 187.0, 208.0) / 255.0 * coverage.b, coverage.b);
        vec4 tree = foliage + trunk * (1.0 - foliage.a);
        ${webgl2 ? 'fragColor' : 'gl_FragColor'} = (blooms + tree * (1.0 - blooms.a)) * u_opacity * v_reach;
        `}
      }`
    const program = gl.createProgram()!
    for (const [type, source] of [[gl.VERTEX_SHADER, vertex], [gl.FRAGMENT_SHADER, fragment]] as const) {
      const shader = gl.createShader(type)!
      gl.shaderSource(shader, source)
      gl.compileShader(shader)
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
        const error = gl.getShaderInfoLog(shader)
        gl.deleteShader(shader); gl.deleteProgram(program)
        throw new Error(`Crown shader: ${error}`)
      }
      gl.attachShader(program, shader)
      gl.deleteShader(shader)
    }
    gl.linkProgram(program)
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const error = gl.getProgramInfoLog(program)
      gl.deleteProgram(program)
      throw new Error(`Crown shader link: ${error}`)
    }
    this.program = program
    this.buffer = gl.createBuffer()!
    this.attributes = ['a_center', 'a_corner', 'a_radius', 'a_color', 'a_uv', 'a_shape'].map(name => gl.getAttribLocation(program, name))
    this.uniforms = Object.fromEntries(['u_matrix', 'u_camera', 'u_budget_camera', 'u_meters', 'u_budget_distance', 'u_opacity', 'u_distance', 'u_viewport', 'u_right', 'u_reach']
      .map(name => [name, gl.getUniformLocation(program, name)]))
    this.textures = []
    if (this.sprites) {
      for (const canvas of [this.sprites.distance]) {
        const texture = gl.createTexture()!
        gl.bindTexture(gl.TEXTURE_2D, texture)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false)
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.NONE)
        gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas)
        gl.pixelStorei(gl.UNPACK_COLORSPACE_CONVERSION_WEBGL, gl.BROWSER_DEFAULT_WEBGL)
        this.textures.push(texture)
      }
    }
  }

  render(gl: GL, matrix: Parameters<CustomLayerInterface['render']>[1]) {
    if (!this.map || !this.program || !this.count) return
    const opacity = this.simplified ? crownZoomOpacity(this.map.getZoom()) : spriteOpacity(this.map.getZoom())
    if (!opacity) return
    const camera = this.camera()
    if (this.simplified && camera.altitude >= CROWN_DISTANCE_MAX) return
    const [cx, cy] = mercatorPoint(camera.lng, camera.lat)
    const meters = metersPerMercatorUnit(camera.lat)
    // Translate in double precision before converting the matrix to float32.
    const local = new Float32Array(matrix)
    for (let row = 0; row < 4; row++) {
      local[12 + row] = matrix[12 + row] + matrix[row] * this.origin[0] + matrix[4 + row] * this.origin[1]
    }
    gl.useProgram(this.program)
    gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer!)
    if (this.uploadedAt !== undefined) {
      // The frame that carried the last upload: the driver defers the copy, so bufferData's own
      // time says little, but a large one stretches that frame.
      this.debug.lastUploadMs = Math.max(0, performance.now() - this.uploadedAt - FRAME_MS)
      this.uploadedAt = undefined
      this.adjustBudget(this.debug.lastUploadMs)
    }
    if (this.upload) {
      gl.bufferData(gl.ARRAY_BUFFER, this.data, gl.DYNAMIC_DRAW)
      this.upload = false
      this.uploadedAt = performance.now()
      this.map.triggerRepaint()
    }
    for (const [i, size, offset] of [[0, 2, 0], [1, 2, 2], [2, 1, 4], [3, 4, 5], [4, 2, 9], [5, 4, 11]]) {
      if (this.attributes[i] < 0) continue
      gl.enableVertexAttribArray(this.attributes[i])
      gl.vertexAttribPointer(this.attributes[i], size, gl.FLOAT, false, STRIDE * 4, offset * 4)
    }
    gl.uniformMatrix4fv(this.uniforms.u_matrix, false, local)
    gl.uniform3f(this.uniforms.u_camera, cx - this.origin[0], cy - this.origin[1], camera.altitude / meters)
    gl.uniform3fv(this.uniforms.u_budget_camera, this.budgetCamera)
    gl.uniform1f(this.uniforms.u_budget_distance, this.budgetDistance)
    gl.uniform1f(this.uniforms.u_meters, meters)
    gl.uniform1f(this.uniforms.u_reach, Number.isFinite(this.reach) ? this.reach : 1e9)
    const arrival = Math.min(1, (performance.now() - this.appearedAt) / 300)
    gl.uniform1f(this.uniforms.u_opacity, opacity * (this.simplified ? arrival : 1))
    if (!this.simplified) {
      this.textures.forEach((texture, i) => { gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, texture) })
      gl.uniform1i(this.uniforms.u_distance, 0)
      gl.uniform2f(this.uniforms.u_viewport, this.map.getCanvas().clientWidth, this.map.getCanvas().clientHeight)
      const bearing = this.map.getBearing() * Math.PI / 180
      gl.uniform2f(this.uniforms.u_right, Math.cos(bearing), Math.sin(bearing))
    }
    gl.drawArrays(gl.TRIANGLES, 0, this.count)
    // MapLibre unbinds its VAO before custom drawing. Leave that default VAO
    // clean, including when a desktop layer is replaced by the mobile shader.
    for (const attribute of this.attributes) if (attribute >= 0) gl.disableVertexAttribArray(attribute)
    if (this.simplified && arrival < 1) this.map.triggerRepaint()
  }

  onRemove(_map: TreeMap, gl: GL) {
    this.generation++
    clearTimeout(this.timer)
    this.timer = undefined
    this.map?.off('move', this.schedule)
    this.map?.off('webglcontextrestored', this.restore)
    if (this.buffer) gl.deleteBuffer(this.buffer)
    if (this.program) gl.deleteProgram(this.program)
    for (const texture of this.textures) gl.deleteTexture(texture)
    this.textures = []
    this.map = undefined
    this.gl = undefined
    this.data = new Float32Array()
    this.drawn = emptyCrownBatch()
    this.drawnTrees = undefined
    this.count = 0
    this.coverage = undefined
    this.loadedFootprint = undefined
    this.queryCamera = undefined
  }
}
