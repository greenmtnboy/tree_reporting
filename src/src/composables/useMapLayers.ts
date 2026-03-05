import type { Ref, ComputedRef } from 'vue'
import maplibregl from 'maplibre-gl'
import { registerCategoryColoredIcons } from './useTreeCategories'
import type { TreeCategory } from '../types'

// --- Exported constants ---

export const TREES_SOURCE_MAXZOOM = 16

// --- Internal constants ---

const MAX_ZOOM = 19

const TREE_CATEGORIES: TreeCategory[] = ['palm', 'broadleaf', 'spreading', 'coniferous', 'columnar', 'ornamental', 'default']

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
const CIRCLE_ZOOM_RADIUS_MAX = 20
const CIRCLE_ZOOM_MAX = 15.5
const CIRCLE_ZOOM_OPACITY_START = CIRCLE_ZOOM_MIN
const CIRCLE_ZOOM_OPACITY_MID = CIRCLE_ZOOM_RADIUS_MID
const CIRCLE_ZOOM_OPACITY_END = CIRCLE_ZOOM_MAX

const ICON_ZOOM_MIN = 14.4
const ICON_ZOOM_SIZE_MID = 15
const ICON_ZOOM_SIZE_HIGH = 18
const ICON_ZOOM_SIZE_MAX = 20
const ICON_ZOOM_OPACITY_START = ICON_ZOOM_MIN
const ICON_ZOOM_OPACITY_MID = ICON_ZOOM_SIZE_MID
const ICON_ZOOM_OPACITY_END = ICON_ZOOM_SIZE_MAX

const HEATMAP_OPACITY_NORMAL: any[] = [
  'interpolate', ['linear'], ['zoom'],
  HEATMAP_ZOOM_OPACITY_START, 0.48,
  HEATMAP_ZOOM_OPACITY_MID, 0.33,
  HEATMAP_ZOOM_OPACITY_END, 0,
]

// --- Expression builders ---

function buildCircleColorExpression(): maplibregl.ExpressionSpecification {
  return ['coalesce', ['get', 'display_color'], '#66BB6A'] as maplibregl.ExpressionSpecification
}

function buildIconExpression(): maplibregl.ExpressionSpecification {
  return [
    'concat', 'tree-', ['coalesce', ['get', 'category'], 'default'], '-',
    ['coalesce', ['get', 'display_color'], '#66BB6A'],
  ] as unknown as maplibregl.ExpressionSpecification
}

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

function buildIconSizeExpression(): maplibregl.ExpressionSpecification {
  return [
    'interpolate', ['linear'], ['zoom'],
    ICON_ZOOM_MIN, buildSqrtDbhExpression(0.04, 0.1),
    ICON_ZOOM_SIZE_MID, buildSqrtDbhExpression(0.055, 0.15),
    ICON_ZOOM_SIZE_HIGH, buildSqrtDbhExpression(0.2, 0.72),
    ICON_ZOOM_SIZE_MAX, buildSqrtDbhExpression(0.28, 1.0),
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

// --- Composable ---

export interface UseMapLayersOptions {
  map: Ref<maplibregl.Map | null>
  simplified: boolean
  activeHeatmapColors: ComputedRef<string[]>
  mapQueryRevision: Ref<number>
}

export function useMapLayers({ map, simplified, activeHeatmapColors, mapQueryRevision }: UseMapLayersOptions) {
  let lastBuiltHeatmapColors: string[] = []
  let treesSourceReloadNonce = 0

  function nowMs(): number {
    return typeof performance !== 'undefined' ? performance.now() : Date.now()
  }

  function removeOldHeatmapLayers(mapInstance: maplibregl.Map) {
    for (const hex of lastBuiltHeatmapColors) {
      if (mapInstance.getLayer(`trees-heat-${hex}`)) mapInstance.removeLayer(`trees-heat-${hex}`)
    }
    // Remove any legacy category-based layers
    for (const cat of TREE_CATEGORIES) {
      if (mapInstance.getLayer(`trees-heat-${cat}`)) mapInstance.removeLayer(`trees-heat-${cat}`)
    }
    lastBuiltHeatmapColors = []
  }

  function requestTreesSourceReload() {
    if (!map.value) return
    const source = map.value.getSource('trees') as any
    if (source && typeof source.reload === 'function') source.reload()
    map.value.triggerRepaint()
  }

  function addTreeLayers() {
    if (!map.value) return
    const mapInstance = map.value
    const t0 = nowMs()
    console.info('[Perf] map:layers:add:start')

    const treeTiles = [`duckdb://trees/{z}/{x}/{y}.pbf?r=${mapQueryRevision.value}&n=${treesSourceReloadNonce}`]
    const heatColors = activeHeatmapColors.value
    const heatLayerIds = heatColors.map((hex) => `trees-heat-${hex}`)

    const existingSource = mapInstance.getSource('trees') as any
    const hasHeatLayers = heatLayerIds.length > 0 && heatLayerIds.every((id) => !!mapInstance.getLayer(id))
    const hasCircleLayer = !!mapInstance.getLayer('trees-circle')
    const hasIconLayer = !!mapInstance.getLayer('trees-icon')
    const hasAllLayers = hasHeatLayers && hasCircleLayer && (simplified || hasIconLayer)

    // Prefer in-place source URL refresh to avoid tearing down layers.
    if (existingSource && hasAllLayers) {
      if (typeof existingSource.setTiles === 'function') {
        existingSource.setTiles(treeTiles)
        if (typeof existingSource.reload === 'function') existingSource.reload()
        console.info('[Perf] map:layers:source-refresh', { ms: Math.round(nowMs() - t0), revision: mapQueryRevision.value })
        return
      }
    }

    if (mapInstance.getLayer('trees-icon')) mapInstance.removeLayer('trees-icon')
    if (mapInstance.getLayer('trees-circle')) mapInstance.removeLayer('trees-circle')
    removeOldHeatmapLayers(mapInstance)
    if (mapInstance.getSource('trees')) mapInstance.removeSource('trees')

    mapInstance.addSource('trees', {
      type: 'vector',
      tiles: treeTiles,
      minzoom: 0,
      // Pin detailed requests at z16; let MapLibre overzoom above.
      maxzoom: TREES_SOURCE_MAXZOOM,
    })

    // Layer 1: Heatmaps per active display_color
    for (const hexColor of heatColors) {
      mapInstance.addLayer({
        id: `trees-heat-${hexColor}`,
        type: 'heatmap',
        source: 'trees',
        'source-layer': 'trees',
        maxzoom: HEATMAP_ZOOM_OPACITY_END,
        filter: ['==', ['coalesce', ['get', 'display_color'], '#66BB6A'], hexColor],
        paint: buildHeatmapLayerPaint(hexColor),
      })
    }
    lastBuiltHeatmapColors = [...heatColors]

    // Layer 2: Colored circles at medium zoom
    mapInstance.addLayer({
      id: 'trees-circle',
      type: 'circle',
      source: 'trees',
      'source-layer': 'trees',
      minzoom: CIRCLE_ZOOM_MIN,
      ...(simplified ? {} : { maxzoom: CIRCLE_ZOOM_MAX }),
      paint: {
        'circle-radius': [...buildCircleRadiusExpression()],
        'circle-color': buildCircleColorExpression(),
        'circle-opacity': simplified
          ? ['interpolate', ['linear'], ['zoom'],
            CIRCLE_ZOOM_OPACITY_START, 0,
            CIRCLE_ZOOM_OPACITY_START + 0.1, 0.85,
            MAX_ZOOM, 0.92] as any
          : ['interpolate', ['linear'], ['zoom'],
            CIRCLE_ZOOM_OPACITY_START, 0,
            CIRCLE_ZOOM_OPACITY_START + 0.1, 0.75,
            CIRCLE_ZOOM_OPACITY_MID, 0.92,
            CIRCLE_ZOOM_OPACITY_END - 0.1, 0.75,
            CIRCLE_ZOOM_OPACITY_END, 0],
        'circle-pitch-alignment': 'map',
        'circle-pitch-scale': 'map',
        'circle-stroke-width': 0.65,
        'circle-stroke-color': 'rgba(255,255,255,0)',
      },
    })

    // Layer 3: Tree icons at close zoom (skipped in simplified mode)
    if (!simplified) {
      if (heatColors.length > 0) registerCategoryColoredIcons(mapInstance, heatColors)
      mapInstance.addLayer({
        id: 'trees-icon',
        type: 'symbol',
        source: 'trees',
        'source-layer': 'trees',
        minzoom: ICON_ZOOM_MIN,
        layout: {
          'icon-image': buildIconExpression(),
          'icon-size': [...buildIconSizeExpression()],
          'icon-rotate': ['get', 'rotation'],
          'icon-rotation-alignment': 'viewport',
          'icon-pitch-alignment': 'viewport',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
        },
        paint: {
          'icon-opacity': ['interpolate', ['linear'], ['zoom'],
            ICON_ZOOM_OPACITY_START, 0,
            ICON_ZOOM_OPACITY_MID, 0.72,
            ICON_ZOOM_OPACITY_END, 1],
        },
      })
    }

    console.info('[Perf] map:layers:add:done', { ms: Math.round(nowMs() - t0) })
  }

  function forceTreesTileRefetchPass() {
    treesSourceReloadNonce += 1
    addTreeLayers()
    map.value?.triggerRepaint()
  }

  // Sync layers to the current color state. Rebuilds heatmap layers when the set
  // of active colors changes. Registers colored icons for all active colors.
  function applyColorToLayers() {
    if (!map.value) return
    const mapInstance = map.value
    const colors = activeHeatmapColors.value

    if (colors.length > 0) registerCategoryColoredIcons(mapInstance, colors)

    if (!simplified && mapInstance.getLayer('trees-icon')) {
      mapInstance.setLayoutProperty('trees-icon', 'icon-image', buildIconExpression())
    }

    if (mapInstance.getLayer('trees-circle')) {
      mapInstance.setPaintProperty('trees-circle', 'circle-color', buildCircleColorExpression())
    }

    const colorsChanged = colors.length !== lastBuiltHeatmapColors.length
      || colors.some((c, i) => c !== lastBuiltHeatmapColors[i])

    if (colorsChanged && mapInstance.getSource('trees')) {
      removeOldHeatmapLayers(mapInstance)
      for (const hexColor of colors) {
        mapInstance.addLayer({
          id: `trees-heat-${hexColor}`,
          type: 'heatmap',
          source: 'trees',
          'source-layer': 'trees',
          maxzoom: HEATMAP_ZOOM_OPACITY_END,
          filter: ['==', ['coalesce', ['get', 'display_color'], '#66BB6A'], hexColor],
          paint: buildHeatmapLayerPaint(hexColor),
        })
      }
      lastBuiltHeatmapColors = [...colors]
      if (mapInstance.getLayer('trees-circle')) {
        for (const hex of colors) mapInstance.moveLayer(`trees-heat-${hex}`, 'trees-circle')
      }
    } else {
      // Just refresh heatmap opacity
      for (const hex of lastBuiltHeatmapColors) {
        const id = `trees-heat-${hex}`
        if (!mapInstance.getLayer(id)) continue
        mapInstance.setPaintProperty(id, 'heatmap-opacity', [...HEATMAP_OPACITY_NORMAL])
      }
    }
  }

  return {
    addTreeLayers,
    applyColorToLayers,
    requestTreesSourceReload,
    forceTreesTileRefetchPass,
  }
}
