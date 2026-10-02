import type { CustomLayerInterface, Map as TreeMap } from 'maplibre-gl'
import { createTreeSpriteAtlas } from './treeSpriteAtlas'
import {
  CROWN_DISTANCE_FULL, CROWN_DISTANCE_MAX, MAX_CROWNS, MAX_TREE_SPRITES, CROWN_ZOOM_START,
  crownZoomOpacity, mercatorPoint, metersPerMercatorUnit, validCrownWidth,
  spriteCrownBlend, spriteBaseScale, spriteOpacity, SPRITE_CROWN_ZOOM_START,
  type CrownCamera, type TreeRenderView, type TreeCrown,
} from '../lib/treeCrowns'

type GL = WebGLRenderingContext | WebGL2RenderingContext
type LoadCrowns = (camera: TreeRenderView) => Promise<TreeCrown[]>
const STRIDE = 14
const CORNERS = [-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]
type ViewBounds = NonNullable<TreeRenderView['bounds']>

function padBounds(bounds: ViewBounds, fraction: number): ViewBounds {
  const x = (bounds.east - bounds.west) * fraction
  const y = (bounds.north - bounds.south) * fraction
  return { west: bounds.west - x, east: bounds.east + x, south: Math.max(-85.05112878, bounds.south - y), north: Math.min(85.05112878, bounds.north + y) }
}

function contains(outer: ViewBounds, inner: ViewBounds): boolean {
  return outer.west <= inner.west && outer.east >= inner.east && outer.south <= inner.south && outer.north >= inner.north
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
  private sprites?: ReturnType<typeof createTreeSpriteAtlas>
  private textures: WebGLTexture[] = []
  private drawnCrowns: TreeCrown[] = []
  private coverage?: ViewBounds
  private coverageBearing = 0
  private get minZoom() { return this.simplified ? CROWN_ZOOM_START : SPRITE_CROWN_ZOOM_START }

  constructor(private load: LoadCrowns, private simplified: boolean) {}

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

  private viewport(): ViewBounds {
    const bounds = this.map!.getBounds()
    return { west: bounds.getWest(), south: bounds.getSouth(), east: bounds.getEast(), north: bounds.getNorth() }
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
      const bounds = this.viewport()
      // Keep a margin for silhouettes anchored offscreen and start fetching
      // before panning consumes it. Reuse only complete (uncapped) batches.
      if (this.coverage && this.coverageBearing === this.map.getBearing()
        && contains(this.coverage, padBounds(bounds, 0.1))) return
      camera.bounds = padBounds(bounds, 0.3)
      camera.visibleBounds = bounds
    }
    this.inFlight = true
    const generation = this.generation
    try {
      const crowns = await this.load(camera)
      if (!this.map || generation !== this.generation) return
      if (this.map.getZoom() <= this.minZoom || (this.simplified && this.camera().altitude >= CROWN_DISTANCE_MAX)) return
      if (camera.bounds && !contains(camera.bounds, this.viewport())) {
        // Keep the last useful batch while catching up; never replace it with
        // a late snapshot of a viewport the user has already left.
        this.dirty = true
        return
      }
      this.setCrowns(crowns, camera)
      this.coverage = camera.bounds && crowns.length < MAX_TREE_SPRITES ? camera.bounds : undefined
      this.coverageBearing = this.map.getBearing()
    } catch (error) {
      if (this.map && generation === this.generation) {
        // A transient query error must not blank an otherwise usable batch.
        console.warn('[TreeCrowns] nearby crowns unavailable', error)
      }
    } finally {
      this.inFlight = false
      if (this.dirty) this.schedule()
    }
  }

  private setCrowns(crowns: TreeCrown[], camera: CrownCamera) {
    const wasEmpty = this.count === 0
    this.origin = mercatorPoint(camera.lng, camera.lat)
    const meters = metersPerMercatorUnit(camera.lat)
    this.budgetCamera = [0, 0, camera.altitude / meters]
    this.budgetDistance = CROWN_DISTANCE_MAX
    this.drawnCrowns = []
    const nearbyDistances: number[] = []
    // Back-to-front for overlapping upright sprites. This is bounded work at
    // data refresh time, never a feature scan on the animation frame.
    const ordered = crowns.slice(0, this.simplified ? MAX_CROWNS : MAX_TREE_SPRITES)
    if (!this.simplified) {
      // Ground-plane depth order depends on bearing, not perspective divide.
      // Sorting on this axis avoids projecting every tree and allocating a Map.
      const bearing = this.map!.getBearing() * Math.PI / 180
      const sin = Math.sin(bearing), cos = Math.cos(bearing)
      const depths = ordered.map(crown => {
        const [x, y] = mercatorPoint(crown.lng, crown.lat)
        return { crown, depth: -x * sin + y * cos }
      }).sort((a, b) => a.depth - b.depth)
      for (let i = 0; i < ordered.length; i++) ordered[i] = depths[i].crown
    }
    const vertices = new Float32Array(ordered.length * 6 * STRIDE)
    let cursor = 0
    for (const crown of ordered) {
      if ((this.simplified && !validCrownWidth(crown.width)) || ![crown.lng, crown.lat].every(Number.isFinite)) continue
      const [x, y] = mercatorPoint(crown.lng, crown.lat)
      // Keep positions local to avoid float32 jitter at street-level zoom.
      const dx = x - this.origin[0], dy = y - this.origin[1]
      const distance = Math.hypot(dx * meters, dy * meters, camera.altitude)
      if (this.simplified && distance >= CROWN_DISTANCE_MAX) continue
      if (validCrownWidth(crown.width) && distance < CROWN_DISTANCE_MAX) nearbyDistances.push(distance)
      const radius = validCrownWidth(crown.width) ? crown.width / 2 / metersPerMercatorUnit(crown.lat) : 0
      const hex = /^#[0-9a-f]{6}$/i.test(crown.color) ? crown.color : '#66BB6A'
      const rgb = [1, 3, 5].map(offset => parseInt(hex.slice(offset, offset + 2), 16) / 255)
      const cell = this.sprites?.cells.get(crown.category ?? 'default') ?? this.sprites?.cells.get('default')
      const dbh = Number.isFinite(crown.dbh) && crown.dbh! > 0 ? crown.dbh! : 3
      for (let i = 0; i < CORNERS.length; i += 2) {
        const cx = CORNERS[i], cy = CORNERS[i + 1]
        vertices[cursor++] = dx
        vertices[cursor++] = dy
        vertices[cursor++] = cx
        vertices[cursor++] = cy
        vertices[cursor++] = radius
        vertices[cursor++] = rgb[0]
        vertices[cursor++] = rgb[1]
        vertices[cursor++] = rgb[2]
        vertices[cursor++] = crown.measured ? 1 : 0
        vertices[cursor++] = cell ? (cx < 0 ? cell.left : cell.right) : 0
        vertices[cursor++] = cell ? (cy > 0 ? cell.top : cell.bottom) : 0
        vertices[cursor++] = cell?.crownFraction ?? 1
        vertices[cursor++] = Math.min(1, Math.sqrt(dbh / 42))
        vertices[cursor++] = cell?.base ?? 0.95
      }
      this.drawnCrowns.push(crown)
    }
    // If density exhausts the budget, fade the outer band instead of showing
    // an arbitrary hard edge through a dense park.
    if (nearbyDistances.length >= MAX_CROWNS) {
      nearbyDistances.sort((a, b) => a - b)
      this.budgetDistance = nearbyDistances[MAX_CROWNS - 1]
    }
    this.data = vertices.subarray(0, cursor)
    this.count = cursor / STRIDE
    this.upload = true
    if (wasEmpty) this.appearedAt = performance.now()
    this.map?.triggerRepaint()
  }

  /** Match the visible, scaled silhouette rather than the old marker bounds. */
  pick(point: { x: number; y: number }): TreeCrown | undefined {
    if (this.simplified || !this.map || !this.count) return
    const map = this.map, camera = this.camera(), zoom = map.getZoom()
    if (zoom <= this.minZoom) return
    const matrix = map.transform.customLayerMatrix()
    const width = map.getCanvas().clientWidth, height = map.getCanvas().clientHeight
    const [cameraX, cameraY] = mercatorPoint(camera.lng, camera.lat)
    const meters = metersPerMercatorUnit(camera.lat)
    const bearing = map.getBearing() * Math.PI / 180
    const rightClip = matrix[0] * Math.cos(bearing) + matrix[4] * Math.sin(bearing)
    const [low, high] = spriteBaseScale(zoom)
    const smooth = (lo: number, hi: number, value: number) => {
      const x = Math.max(0, Math.min(1, (value - lo) / (hi - lo)))
      return x * x * (3 - 2 * x)
    }
    const zoomFade = spriteCrownBlend(zoom)
    for (let i = this.drawnCrowns.length - 1; i >= 0; i--) {
      const crown = this.drawnCrowns[i]
      const cell = this.sprites!.cells.get(crown.category ?? 'default') ?? this.sprites!.cells.get('default')!
      const offset = i * STRIDE * 6
      const x = this.data[offset] + this.origin[0], y = this.data[offset + 1] + this.origin[1]
      const w = matrix[3] * x + matrix[7] * y + matrix[15]
      if (w <= 0) continue
      const sx = ((matrix[0] * x + matrix[4] * y + matrix[12]) / w + 1) * width / 2
      const sy = (1 - (matrix[1] * x + matrix[5] * y + matrix[13]) / w) * height / 2
      const distance = Math.hypot(x - cameraX, y - cameraY, camera.altitude / meters) * meters
      const budget = Math.hypot(x - this.origin[0], y - this.origin[1], this.budgetCamera[2]) * meters
      const fade = (validCrownWidth(crown.width) ? zoomFade : 0) * (1 - smooth(CROWN_DISTANCE_FULL, CROWN_DISTANCE_MAX, distance)) * (1 - smooth(this.budgetDistance * 0.85, this.budgetDistance, budget))
      const base = 48 * (low + (high - low) * this.data[offset + 12]) * Math.max(0, Math.min(4, 0.5 + 0.5 * map.transform.cameraToCenterDistance / w))
      const target = Math.abs(rightClip * this.data[offset + 4] / cell.crownFraction) / w * width
      const size = base * Math.pow(Math.max(0.01, target) / base, fade)
      const u = (point.x - sx) / size + 0.5, v = (point.y - sy) / size + cell.base
      if (u < 0 || u >= 1 || v < 0 || v >= 1) continue
      if (cell.alpha[Math.floor(v * cell.size) * cell.size + Math.floor(u * cell.size)] > 32) return crown
    }
  }

  /** Read-only diagnostics/click candidates; custom layers have no MapLibre hit index. */
  get trees(): readonly TreeCrown[] { return this.count ? this.drawnCrowns : [] }

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
      ${attribute} vec3 a_shape;
      uniform vec2 u_viewport;
      uniform vec2 u_right;
      uniform vec2 u_base_scale;
      uniform float u_center_distance;
      uniform float u_opacity;
      ${webgl2 ? 'out' : 'varying'} vec2 v_uv;` : ''}
      uniform mat4 u_matrix;
      uniform vec3 u_camera;
      uniform vec3 u_budget_camera;
      uniform float u_meters;
      uniform float u_budget_distance;
      ${webgl2 ? 'out' : 'varying'} vec2 v_corner;
      ${webgl2 ? 'out' : 'varying'} vec4 v_color;
      ${webgl2 ? 'out' : 'varying'} float v_fade;
      void main() {
        float distance = length(vec3(a_center, 0.0) - u_camera) * u_meters;
        float budget = length(vec3(a_center, 0.0) - u_budget_camera) * u_meters;
        v_fade = (1.0 - smoothstep(${CROWN_DISTANCE_FULL}.0, ${CROWN_DISTANCE_MAX}.0, distance))
          * (1.0 - smoothstep(u_budget_distance * 0.85, u_budget_distance, budget));
        if (a_radius <= 0.0) v_fade = 0.0;
        v_corner = a_corner;
        v_color = a_color;
        ${this.simplified ? `gl_Position = v_fade > 0.0
          ? u_matrix * vec4(a_center + a_corner * a_radius, 0.0, 1.0)
          : vec4(2.0, 2.0, 2.0, 1.0);` : `
        vec4 anchor = u_matrix * vec4(a_center, 0.0, 1.0);
        float perspective = clamp(0.5 + 0.5 * u_center_distance / anchor.w, 0.0, 4.0);
        float basePixels = 48.0 * mix(u_base_scale.x, u_base_scale.y, a_shape.y) * perspective;
        float baseHalfWidth = basePixels / u_viewport.x * anchor.w;
        vec4 crownRight = u_matrix * vec4(u_right * a_radius / a_shape.x, 0.0, 0.0);
        // Geometric interpolation makes each increment a proportional size
        // change rather than a sudden jump toward a much larger crown.
        float halfWidth = baseHalfWidth * pow(max(0.000001, abs(crownRight.x)) / baseHalfWidth, v_fade * u_opacity);
        // Separate dimensions: a future height model can replace halfHeight
        // independently. For now retain each sprite's aspect ratio.
        float halfHeight = halfWidth * u_viewport.x / u_viewport.y;
        anchor.xy += vec2(a_corner.x * halfWidth, (a_corner.y + 2.0 * a_shape.z - 1.0) * halfHeight);
        gl_Position = anchor.w > 0.0 ? anchor : vec4(2.0, 2.0, 2.0, 1.0);
        v_uv = a_uv;`}
      }`
    const fragment = `${header}${!webgl2 && derivatives ? '#extension GL_OES_standard_derivatives : enable\n' : ''}
      precision highp float;
      ${webgl2 ? 'in' : 'varying'} vec2 v_corner;
      ${webgl2 ? 'in' : 'varying'} vec4 v_color;
      ${webgl2 ? 'in' : 'varying'} float v_fade;
      uniform float u_opacity;
      ${!this.simplified ? `uniform sampler2D u_distance;
      uniform float u_sprite_opacity;
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
        ${webgl2 ? 'fragColor' : 'gl_FragColor'} = (blooms + tree * (1.0 - blooms.a)) * u_sprite_opacity;
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
    this.uniforms = Object.fromEntries(['u_matrix', 'u_camera', 'u_budget_camera', 'u_meters', 'u_budget_distance', 'u_opacity', 'u_distance', 'u_viewport', 'u_right', 'u_base_scale', 'u_center_distance', 'u_sprite_opacity']
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
    const opacity = this.simplified ? crownZoomOpacity(this.map.getZoom()) : spriteCrownBlend(this.map.getZoom())
    if (this.simplified ? !opacity : !spriteOpacity(this.map.getZoom())) return
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
    if (this.upload) { gl.bufferData(gl.ARRAY_BUFFER, this.data, gl.DYNAMIC_DRAW); this.upload = false }
    for (const [i, size, offset] of [[0, 2, 0], [1, 2, 2], [2, 1, 4], [3, 4, 5], [4, 2, 9], [5, 3, 11]]) {
      if (this.attributes[i] < 0) continue
      gl.enableVertexAttribArray(this.attributes[i])
      gl.vertexAttribPointer(this.attributes[i], size, gl.FLOAT, false, STRIDE * 4, offset * 4)
    }
    gl.uniformMatrix4fv(this.uniforms.u_matrix, false, local)
    gl.uniform3f(this.uniforms.u_camera, cx - this.origin[0], cy - this.origin[1], camera.altitude / meters)
    gl.uniform3fv(this.uniforms.u_budget_camera, this.budgetCamera)
    gl.uniform1f(this.uniforms.u_budget_distance, this.budgetDistance)
    gl.uniform1f(this.uniforms.u_meters, meters)
    const arrival = Math.min(1, (performance.now() - this.appearedAt) / 300)
    gl.uniform1f(this.uniforms.u_opacity, opacity * (this.simplified ? arrival : 1))
    if (!this.simplified) {
      this.textures.forEach((texture, i) => { gl.activeTexture(gl.TEXTURE0 + i); gl.bindTexture(gl.TEXTURE_2D, texture) })
      gl.uniform1i(this.uniforms.u_distance, 0)
      gl.uniform2f(this.uniforms.u_viewport, this.map.getCanvas().clientWidth, this.map.getCanvas().clientHeight)
      const bearing = this.map.getBearing() * Math.PI / 180
      gl.uniform2f(this.uniforms.u_right, Math.cos(bearing), Math.sin(bearing))
      const zoom = this.map.getZoom()
      const [low, high] = spriteBaseScale(zoom)
      gl.uniform2f(this.uniforms.u_base_scale, low, high)
      gl.uniform1f(this.uniforms.u_center_distance, this.map.transform.cameraToCenterDistance)
      gl.uniform1f(this.uniforms.u_sprite_opacity, spriteOpacity(zoom))
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
    this.drawnCrowns = []
    this.count = 0
    this.coverage = undefined
  }
}
