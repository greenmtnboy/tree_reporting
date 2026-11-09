import type { Ref, ComputedRef } from 'vue'
import maplibregl from 'maplibre-gl'
import type { Landmark, TreeForm } from '../types'
import { TreeCrownLayer } from './treeCrownLayer'
import { SPRITE_CROWN_ZOOM_START, SPRITE_OPAQUE_ZOOM } from '../lib/treeCrowns'
import { TreeDotsLayer, type TreeDotsPick } from './treeDotsLayer'
import { useDuckDB } from './useDuckDB'

// --- Exported constants ---

export const TREES_SOURCE_MAXZOOM = 16
// Below this zoom no tree tiles are requested; the city markers carry the map
// on their own. The worker grid-aggregates every z <= 13 tile at 32m, so these
// coarse tiles stay cheap (a whole city fits in a single tile at z6).
export const TREES_SOURCE_MINZOOM = 6
export const LANDMARK_ZOOM_MIN = 11

// --- Internal constants ---

const MAX_ZOOM = 21

const TREE_CATEGORIES: TreeForm[] = [
  'broadleaf',
  'conifer',
  'palm',
  'columnar',
  'ornamental',
  'spreading',
  'weeping',
  'multi_trunk',
  'default',
]

const HEATMAP_ZOOM_INTENSITY_START = 10
const HEATMAP_ZOOM_INTENSITY_MID = 13
const HEATMAP_ZOOM_INTENSITY_END = 15
const HEATMAP_ZOOM_RADIUS_START = 10
const HEATMAP_ZOOM_RADIUS_MID = 13
const HEATMAP_ZOOM_RADIUS_END = 15
const HEATMAP_ZOOM_OPACITY_START = HEATMAP_ZOOM_RADIUS_START
const HEATMAP_ZOOM_OPACITY_MID = HEATMAP_ZOOM_RADIUS_MID
const HEATMAP_ZOOM_OPACITY_END = HEATMAP_ZOOM_RADIUS_END

const CIRCLE_ZOOM_MIN = 12.8
const CIRCLE_ZOOM_RADIUS_MID = 15
const CIRCLE_ZOOM_RADIUS_HIGH = 18
const CIRCLE_ZOOM_RADIUS_MAX = 21
const CIRCLE_ZOOM_MAX = SPRITE_OPAQUE_ZOOM
const CIRCLE_ZOOM_OPACITY_START = CIRCLE_ZOOM_MIN
const CIRCLE_ZOOM_OPACITY_MID = SPRITE_CROWN_ZOOM_START
const CIRCLE_ZOOM_OPACITY_END = CIRCLE_ZOOM_MAX

// Holds full strength down to z8 — a city's urban outline is still legible on
// the basemap around z7, so the heat should still be there to fill it — then
// fades out toward TREES_SOURCE_MINZOOM as the city markers fade in over the
// same 6-8 band (see useGlobeCityMarkers), so zooming out crossfades from heat
// to city dots instead of hitting a blank map.
const HEATMAP_OPACITY_NORMAL: any[] = [
  'interpolate', ['linear'], ['zoom'],
  TREES_SOURCE_MINZOOM, 0,
  TREES_SOURCE_MINZOOM + 2, 0.48,
  HEATMAP_ZOOM_OPACITY_START, 0.48,
  HEATMAP_ZOOM_OPACITY_MID, 0.33,
  HEATMAP_ZOOM_OPACITY_END, 0,
]

// --- Per-colour layers ---

// The worker writes one MVT source layer per colour, named by the colour
// (workers/mvtLayers.ts), and the map draws one heatmap and one circle layer
// per source layer. Each layer's bucket then reads only its own features:
// with a single source layer and a colour filter per layer, MapLibre ran
// every layer's filter over every feature of the tile.
const HEAT_LAYER_PREFIX = 'trees-heat-'
const CIRCLE_LAYER_PREFIX = 'trees-circle-'

function styleLayerIds(mapInstance: maplibregl.Map, prefix: string): string[] {
  return (mapInstance.getStyle()?.layers ?? []).filter((l) => l.id.startsWith(prefix)).map((l) => l.id)
}

/** The circle layers, one per colour: the tree layers a tap or a feature query reads. */
export function treeCircleLayerIds(mapInstance: maplibregl.Map): string[] {
  return styleLayerIds(mapInstance, CIRCLE_LAYER_PREFIX)
}

/** The heatmap layers, one per colour. */
export function treeHeatLayerIds(mapInstance: maplibregl.Map): string[] {
  return styleLayerIds(mapInstance, HEAT_LAYER_PREFIX)
}

/** Features the `trees` source holds for the view, over every colour's source layer. */
export function treeSourceFeatureCount(mapInstance: maplibregl.Map): number {
  if (!mapInstance.getSource('trees')) return 0
  const layers = new Set<string>()
  for (const l of mapInstance.getStyle()?.layers ?? []) {
    if ('source' in l && l.source === 'trees' && 'source-layer' in l && l['source-layer']) layers.add(l['source-layer'])
  }
  let n = 0
  for (const sourceLayer of layers) n += mapInstance.querySourceFeatures('trees', { sourceLayer }).length
  return n
}

// --- Expression builders ---

function buildHeatmapColorExpression(hexColor: string): maplibregl.ExpressionSpecification {
  return [
    'interpolate', ['linear'], ['heatmap-density'],
    0, 'rgba(0,0,0,0)',
    0.05, 'rgba(0,0,0,0)',
    0.15, hexColor,
    0.55, hexColor,
    1, hexColor,
  ] as maplibregl.ExpressionSpecification
}

function buildSqrtDbhExpression(minValue: number, maxValue: number): maplibregl.ExpressionSpecification {
  const sqrtMaxDbh = Math.sqrt(42)
  return [
    'interpolate', ['linear'],
    ['sqrt', ['coalesce', ['to-number', ['get', 'dbh']], 3]],
    0, minValue,
    sqrtMaxDbh, maxValue,
  ] as maplibregl.ExpressionSpecification
}

function buildCircleRadiusExpression(): maplibregl.ExpressionSpecification {
  return [
    'interpolate', ['linear'], ['zoom'],
    CIRCLE_ZOOM_MIN, buildSqrtDbhExpression(0.65, 1),
    CIRCLE_ZOOM_RADIUS_MID, buildSqrtDbhExpression(2.1, 5.6),
    CIRCLE_ZOOM_RADIUS_HIGH, buildSqrtDbhExpression(3.1, 9.5),
    CIRCLE_ZOOM_RADIUS_MAX, buildSqrtDbhExpression(3.7, 11.6),
  ] as maplibregl.ExpressionSpecification
}

function buildHeatmapLayerPaint(hexColor: string): any {
  return {
    'heatmap-weight': [
      'interpolate', ['linear'],
      [
        '*',
        ['coalesce', ['to-number', ['get', 'point_count']], 1],
        ['^', ['/', 32, ['coalesce', ['to-number', ['get', 'grid_m']], 32]], 2],
      ],
      1, 0.2, 8, 0.45, 32, 0.75, 128, 1.05,
    ],
    'heatmap-intensity': [
      'interpolate', ['linear'], ['zoom'],
      HEATMAP_ZOOM_INTENSITY_START, 0.85,
      HEATMAP_ZOOM_INTENSITY_MID, 1.2,
      HEATMAP_ZOOM_INTENSITY_END, 1.9,
    ],
    'heatmap-radius': [
      'interpolate', ['linear'], ['zoom'],
      HEATMAP_ZOOM_RADIUS_START, 10,
      HEATMAP_ZOOM_RADIUS_MID, 14,
      HEATMAP_ZOOM_RADIUS_END, 22,
    ],
    'heatmap-color': buildHeatmapColorExpression(hexColor),
    'heatmap-opacity': [...HEATMAP_OPACITY_NORMAL],
  }
}

function heatLayerSpec(hexColor: string): maplibregl.LayerSpecification {
  return {
    id: `${HEAT_LAYER_PREFIX}${hexColor}`,
    type: 'heatmap',
    source: 'trees',
    'source-layer': hexColor,
    maxzoom: HEATMAP_ZOOM_OPACITY_END,
    paint: buildHeatmapLayerPaint(hexColor),
  }
}

function circleLayerSpec(hexColor: string, simplified: boolean): maplibregl.LayerSpecification {
  return {
    id: `${CIRCLE_LAYER_PREFIX}${hexColor}`,
    type: 'circle',
    source: 'trees',
    'source-layer': hexColor,
    minzoom: CIRCLE_ZOOM_MIN,
    ...(simplified ? {} : { maxzoom: CIRCLE_ZOOM_MAX }),
    paint: {
      'circle-radius': [...buildCircleRadiusExpression()] as maplibregl.ExpressionSpecification,
      'circle-color': hexColor,
      'circle-opacity': (simplified
        ? ['interpolate', ['linear'], ['zoom'],
          CIRCLE_ZOOM_OPACITY_START, 0,
          CIRCLE_ZOOM_OPACITY_START + 0.1, 0.85,
          MAX_ZOOM, 0.92]
        : ['interpolate', ['linear'], ['zoom'],
          CIRCLE_ZOOM_OPACITY_START, 0,
          CIRCLE_ZOOM_OPACITY_START + 0.1, 0.75,
          CIRCLE_ZOOM_OPACITY_MID, 0.92,
          CIRCLE_ZOOM_OPACITY_END - 0.1, 0.75,
          CIRCLE_ZOOM_OPACITY_END, 0]) as maplibregl.ExpressionSpecification,
      'circle-pitch-alignment': 'map',
      'circle-pitch-scale': 'map',
      'circle-stroke-width': 0.65,
      'circle-stroke-color': 'rgba(255,255,255,0)',
    },
  }
}

// --- Landmark eye icon + layer ---

function drawLandmarkEye(ctx: CanvasRenderingContext2D, size: number): void {
  const cx = size / 2
  const cy = size / 2
  const hw = size * 0.40 // half-width of eye almond
  const hh = size * 0.22 // half-height

  // No glow — keep it subtle
  ctx.shadowBlur = 0

  // Almond eye outline
  ctx.beginPath()
  ctx.moveTo(cx - hw, cy)
  ctx.quadraticCurveTo(cx, cy - hh * 2, cx + hw, cy)
  ctx.quadraticCurveTo(cx, cy + hh * 2, cx - hw, cy)
  ctx.closePath()
  ctx.fillStyle = 'rgba(255, 255, 255, 0.06)'
  ctx.fill()
  ctx.strokeStyle = 'rgba(200, 200, 200, 0.65)'
  ctx.lineWidth = 1.2
  ctx.stroke()

  // Clip to eye shape for iris/pupil
  ctx.save()
  ctx.beginPath()
  ctx.moveTo(cx - hw, cy)
  ctx.quadraticCurveTo(cx, cy - hh * 2, cx + hw, cy)
  ctx.quadraticCurveTo(cx, cy + hh * 2, cx - hw, cy)
  ctx.closePath()
  ctx.clip()

  // Iris — muted grey-blue
  const irisR = size * 0.145
  ctx.beginPath()
  ctx.arc(cx, cy, irisR, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(160, 170, 180, 0.75)'
  ctx.fill()

  // Pupil
  const pupilR = size * 0.075
  ctx.beginPath()
  ctx.arc(cx, cy, pupilR, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(0, 0, 0, 0.75)'
  ctx.fill()

  // Highlight
  ctx.beginPath()
  ctx.arc(cx - irisR * 0.32, cy - irisR * 0.32, pupilR * 0.48, 0, Math.PI * 2)
  ctx.fillStyle = 'rgba(255, 255, 255, 0.6)'
  ctx.fill()

  ctx.restore()
}

export function registerLandmarkEyeIcon(mapInstance: maplibregl.Map): void {
  if (mapInstance.hasImage('landmark-eye')) return
  const size = 34
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  drawLandmarkEye(ctx, size)
  const imageData = ctx.getImageData(0, 0, size, size)
  mapInstance.addImage('landmark-eye', {
    width: size,
    height: size,
    data: new Uint8Array(imageData.data.buffer),
  })
}

export function addLandmarkLayer(mapInstance: maplibregl.Map, landmarks: Landmark[]): void {
  const geojson: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: landmarks.map((lm) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [lm.lng, lm.lat] },
      properties: { id: lm.id, name: lm.name },
    })),
  }

  const existingSource = mapInstance.getSource('landmarks') as maplibregl.GeoJSONSource | undefined
  if (existingSource) {
    existingSource.setData(geojson)
    return
  }

  registerLandmarkEyeIcon(mapInstance)

  mapInstance.addSource('landmarks', { type: 'geojson', data: geojson })

  mapInstance.addLayer({
    id: 'landmarks-eye',
    type: 'symbol',
    source: 'landmarks',
    minzoom: LANDMARK_ZOOM_MIN,
    layout: {
      'icon-image': 'landmark-eye',
      'icon-size': [
        'interpolate', ['linear'], ['zoom'],
        LANDMARK_ZOOM_MIN, 0.55,
        14, 0.72,
        17, 0.92,
      ] as any,
      'icon-allow-overlap': false,
      'icon-ignore-placement': false,
      'icon-anchor': 'center',
    },
    paint: {
      'icon-opacity': [
        'interpolate', ['linear'], ['zoom'],
        LANDMARK_ZOOM_MIN, 0,
        LANDMARK_ZOOM_MIN + 0.8, 0.52,
      ] as any,
    },
  })
}

export function removeLandmarkLayer(mapInstance: maplibregl.Map): void {
  if (mapInstance.getLayer('landmarks-eye')) mapInstance.removeLayer('landmarks-eye')
  if (mapInstance.getSource('landmarks')) mapInstance.removeSource('landmarks')
}

// --- Composable ---

export interface UseMapLayersOptions {
  map: Ref<maplibregl.Map | null>
  simplified: boolean
  activeHeatmapColors: ComputedRef<string[]>
  mapQueryRevision: Ref<number>
  /** The resident layer drew a new set of trees (a city, a query or a filter; not a recolour). */
  onResidentTreesLoaded?: (count: number) => void
}

export function useMapLayers({ map, simplified, activeHeatmapColors, mapQueryRevision, onResidentTreesLoaded }: UseMapLayersOptions) {
  let lastBuiltColors: string[] = []
  let treesSourceReloadNonce = 0
  let crownLayer: TreeCrownLayer | undefined
  // The desktop draws every tree from memory (TreeDotsLayer) where WebGL2
  // allows, and so has no vector tile source; a phone, or a desktop without
  // a float render target, keeps the tiles. Decided once, at the first add.
  let residentMode: boolean | undefined
  let dotsLayer: TreeDotsLayer | undefined

  function isResident(): boolean {
    if (residentMode === undefined && map.value) residentMode = !simplified && TreeDotsLayer.supported(map.value)
    return !!residentMode
  }

  /** The first label layer in the style: trees draw under the basemap's place and street names. */
  function firstLabelLayer(mapInstance: maplibregl.Map): string | undefined {
    return mapInstance.getStyle().layers.find(layer => layer.type === 'symbol')?.id
  }

  /** Add the sprite and resident layers if missing, and bring the resident set up to the worker's query and colours. */
  function syncResidentLayers(mapInstance: maplibregl.Map): Promise<void> {
    if (!crownLayer || !mapInstance.getLayer(crownLayer.id)) {
      crownLayer = new TreeCrownLayer(useDuckDB().getCrownBatch, simplified)
      mapInstance.addLayer(crownLayer, firstLabelLayer(mapInstance))
    } else {
      crownLayer.invalidate()
    }
    if (!dotsLayer) {
      const { getPointKeys, getPointChunkDirectory, getPointChunks, getPointChunkColors } = useDuckDB()
      dotsLayer = new TreeDotsLayer({ getPointKeys, getPointChunkDirectory, getPointChunks, getPointChunkColors }, (count) => onResidentTreesLoaded?.(count),
        () => crownLayer?.spriteCoverage() ?? null, () => crownLayer?.busy() ?? false)
    }
    if (!mapInstance.getLayer(dotsLayer.id)) mapInstance.addLayer(dotsLayer, crownLayer.id)
    return dotsLayer.sync()
  }

  function nowMs(): number {
    return typeof performance !== 'undefined' ? performance.now() : Date.now()
  }

  function removeColorLayers(mapInstance: maplibregl.Map) {
    for (const id of [...treeHeatLayerIds(mapInstance), ...treeCircleLayerIds(mapInstance)]) mapInstance.removeLayer(id)
    // Legacy single-source-layer ids.
    for (const id of ['trees-circle', ...TREE_CATEGORIES.map((cat) => `${HEAT_LAYER_PREFIX}${cat}`)]) {
      if (mapInstance.getLayer(id)) mapInstance.removeLayer(id)
    }
    lastBuiltColors = []
  }

  /**
   * One heatmap and one circle layer per colour. The heatmaps sit under the
   * sprites; the circles sit under them on the desktop (whose sprites take
   * over from z13) and over them on a phone, as the single circle layer did.
   */
  function addColorLayers(mapInstance: maplibregl.Map, colors: string[]) {
    const crownId = crownLayer && mapInstance.getLayer(crownLayer.id) ? crownLayer.id : undefined
    for (const hex of colors) mapInstance.addLayer(heatLayerSpec(hex), crownId)
    const circleBefore = simplified
      ? (mapInstance.getLayer('landmarks-eye') ? 'landmarks-eye' : undefined)
      : crownId
    for (const hex of colors) mapInstance.addLayer(circleLayerSpec(hex, simplified), circleBefore)
    lastBuiltColors = [...colors]
  }

  function addTreeLayers(): Promise<void> {
    if (!map.value) return Promise.resolve()
    const mapInstance = map.value
    if (isResident()) return syncResidentLayers(mapInstance)
    crownLayer?.invalidate()
    const t0 = nowMs()
    console.info('[Perf] map:layers:add:start')

    const treeTiles = [`duckdb://trees/{z}/{x}/{y}.pbf?r=${mapQueryRevision.value}&n=${treesSourceReloadNonce}`]
    const colors = activeHeatmapColors.value

    const existingSource = mapInstance.getSource('trees') as any
    const hasColorLayers = colors.length > 0
      && colors.every((hex) => !!mapInstance.getLayer(`${HEAT_LAYER_PREFIX}${hex}`) && !!mapInstance.getLayer(`${CIRCLE_LAYER_PREFIX}${hex}`))
    const hasSpriteLayer = !!mapInstance.getLayer(simplified ? 'trees-crown' : 'trees-icon')
    const hasAllLayers = hasColorLayers && hasSpriteLayer

    // Prefer in-place source URL refresh to avoid tearing down layers.
    if (existingSource && hasAllLayers) {
      if (typeof existingSource.setTiles === 'function') {
        existingSource.setTiles(treeTiles)
        console.info('[Perf] map:layers:source-refresh', { ms: Math.round(nowMs() - t0), revision: mapQueryRevision.value })
        return Promise.resolve()
      }
    }

    if (mapInstance.getLayer('trees-icon')) mapInstance.removeLayer('trees-icon')
    if (mapInstance.getLayer('trees-crown')) mapInstance.removeLayer('trees-crown')
    removeColorLayers(mapInstance)
    if (mapInstance.getSource('trees')) mapInstance.removeSource('trees')

    mapInstance.addSource('trees', {
      type: 'vector',
      promoteId: 'id',
      tiles: treeTiles,
      // Don't request tiles below the zoom where the heatmap becomes visible.
      // Below this zoom the opacity expression renders nothing anyway, and the
      // globe city-marker layer takes over.
      minzoom: TREES_SOURCE_MINZOOM,
      // Pin detailed requests at z16; let MapLibre overzoom above.
      maxzoom: TREES_SOURCE_MAXZOOM,
    })

    crownLayer = new TreeCrownLayer(useDuckDB().getCrownBatch, simplified)
    mapInstance.addLayer(crownLayer, firstLabelLayer(mapInstance))
    addColorLayers(mapInstance, colors)

    console.info('[Perf] map:layers:add:done', { ms: Math.round(nowMs() - t0) })
    return Promise.resolve()
  }

  /**
   * Redraw for a new query, filter or colour. Tiles take a new URL nonce;
   * the resident layer re-reads what changed and keeps drawing meanwhile.
   */
  function forceTreesTileRefetchPass(): Promise<void> {
    treesSourceReloadNonce += 1
    const done = addTreeLayers()
    map.value?.triggerRepaint()
    return done
  }

  // Sync layers to the current colour set: the layers are per colour, so a
  // new set rebuilds them. The sprite shader uses the refreshed color map.
  function applyColorToLayers() {
    if (!map.value || isResident()) return
    const mapInstance = map.value
    const colors = activeHeatmapColors.value

    const colorsChanged = colors.length !== lastBuiltColors.length
      || colors.some((c, i) => c !== lastBuiltColors[i])

    if (colorsChanged && mapInstance.getSource('trees')) {
      removeColorLayers(mapInstance)
      addColorLayers(mapInstance, colors)
    } else {
      // Just refresh heatmap opacity
      for (const id of treeHeatLayerIds(mapInstance)) mapInstance.setPaintProperty(id, 'heatmap-opacity', [...HEATMAP_OPACITY_NORMAL])
    }
  }

  return {
    pickCrownSprite: (point: { x: number; y: number }) => crownLayer?.pick(point),
    /** True when the desktop draws every tree from memory and the tile source does not exist. */
    treesResident: () => isResident(),
    /** Trees the resident layer holds; null before its first set arrives. */
    residentTreeCount: (): number | null => (dotsLayer?.loaded ? dotsLayer.count : null),
    /** The resident circle under a point, while circles are drawn. */
    pickResidentTree: (point: { x: number; y: number }): TreeDotsPick | undefined => dotsLayer?.pick(point),
    /** Trees the sprite layer drew last frame; custom layers have no queryRenderedFeatures. */
    renderedTreeSprites: (): readonly { lng: number; lat: number }[] => crownLayer?.trees ?? [],
    spriteStats: () => crownLayer?.stats() ?? null,
    spriteRadar: () => crownLayer?.radar() ?? null,
    addTreeLayers,
    applyColorToLayers,
    forceTreesTileRefetchPass,
  }
}
