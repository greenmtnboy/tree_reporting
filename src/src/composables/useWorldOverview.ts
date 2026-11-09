import type maplibregl from 'maplibre-gl'
import { BLOCK_ZOOM, blockCenter, type WorldDirectory } from '../workers/worldBlocks'
import { LOAD_MIN_ZOOM } from './treeDotsLayer'

/**
 * Where the world's trees are, drawn from the manifest alone: one soft dot
 * per block, weighted by its tree count.
 *
 * Trees are loaded only for the ground near the camera and never below
 * LOAD_MIN_ZOOM, where a view spans several metros. Without this a metro
 * that is not loaded is blank until the camera is over it, and a flight
 * from one city to the next crosses an empty map. With it every inventory
 * shows at any zoom for the cost of the manifest (a few tens of KB), and
 * the dots fade as the real trees take over.
 */

const SOURCE = 'world-blocks'
export const WORLD_OVERVIEW_LAYER = 'world-blocks-glow'
/** Gone by here: the trees themselves are drawn from LOAD_MIN_ZOOM. */
const OVERVIEW_MAX_ZOOM = LOAD_MIN_ZOOM + 1.5

/** A block's width in CSS pixels at `zoom` (512 px tiles). */
const blockPx = (zoom: number) => 512 * 2 ** (zoom - BLOCK_ZOOM)

function overviewGeoJSON(directory: WorldDirectory): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = []
  for (let i = 0; i < directory.blocks.length; i += 3) {
    features.push({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: blockCenter(directory.blocks[i]) },
      properties: { rows: directory.blocks[i + 1] },
    })
  }
  return { type: 'FeatureCollection', features }
}

/** Add (or refresh) the overview under `beforeId`. */
export function addWorldOverview(map: maplibregl.Map, directory: WorldDirectory, beforeId?: string): void {
  const data = overviewGeoJSON(directory)
  const existing = map.getSource(SOURCE) as maplibregl.GeoJSONSource | undefined
  if (existing) {
    existing.setData(data)
    return
  }
  map.addSource(SOURCE, { type: 'geojson', data })
  map.addLayer({
    id: WORLD_OVERVIEW_LAYER,
    type: 'circle',
    source: SOURCE,
    maxzoom: OVERVIEW_MAX_ZOOM,
    paint: {
      // A little wider than the block, so neighbours merge into one patch; never under 1.5 px.
      'circle-radius': [
        'interpolate', ['exponential', 2], ['zoom'],
        2, Math.max(1.5, blockPx(2) * 0.7),
        5, Math.max(1.5, blockPx(5) * 0.7),
        OVERVIEW_MAX_ZOOM, blockPx(OVERVIEW_MAX_ZOOM) * 0.7,
      ],
      'circle-color': '#66BB6A',
      'circle-blur': 0.9,
      // A handful of trees is a hint, a dense block is solid; fading out as the trees load.
      // (The zoom ramp has to be the outer expression: MapLibre reads zoom only there.)
      'circle-opacity': [
        'interpolate', ['linear'], ['zoom'],
        LOAD_MIN_ZOOM, ['interpolate', ['linear'], ['ln', ['max', 1, ['get', 'rows']]], 0, 0.1, 7, 0.3, 11, 0.6],
        OVERVIEW_MAX_ZOOM, 0,
      ],
    },
  }, beforeId && map.getLayer(beforeId) ? beforeId : undefined)
}

export function removeWorldOverview(map: maplibregl.Map): void {
  if (map.getLayer(WORLD_OVERVIEW_LAYER)) map.removeLayer(WORLD_OVERVIEW_LAYER)
  if (map.getSource(SOURCE)) map.removeSource(SOURCE)
}
