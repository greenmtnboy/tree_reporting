import maplibregl from 'maplibre-gl'
import { CITY_CONFIG, type CityCode } from './useMapData'

// City markers appear below this zoom; fully hidden at or above it.
// Markers fade in over the same 6-8 band the tree heatmap fades out across
// (from TREES_SOURCE_MINZOOM in useMapLayers), so zooming out crossfades from
// heat to city dots with no blank stretch. The dot is fully in by z6, where a
// city's footprint is too small on the basemap for the heat to say anything.
export const GLOBE_MARKERS_MAX_ZOOM = 8

function cityMarkersGeoJSON(selectedCity?: CityCode): GeoJSON.FeatureCollection {
  return {
    type: 'FeatureCollection',
    features: Object.entries(CITY_CONFIG).map(([code, cfg]) => ({
      type: 'Feature' as const,
      geometry: { type: 'Point' as const, coordinates: cfg.center },
      properties: {
        code,
        name: cfg.name,
        selected: code === selectedCity ? 1 : 0,
      },
    })),
  }
}

export function addCityMarkers(mapInstance: maplibregl.Map, selectedCity?: CityCode): void {
  removeCityMarkers(mapInstance)

  mapInstance.addSource('city-markers', {
    type: 'geojson',
    data: cityMarkersGeoJSON(selectedCity),
  })

  // Outer glow ring
  mapInstance.addLayer({
    id: 'city-markers-glow',
    type: 'circle',
    source: 'city-markers',
    maxzoom: GLOBE_MARKERS_MAX_ZOOM,
    paint: {
      'circle-radius': [
        'interpolate', ['linear'], ['zoom'],
        2, 10,
        GLOBE_MARKERS_MAX_ZOOM - 1, 28,
      ] as any,
      'circle-color': [
        'case', ['==', ['get', 'selected'], 1], '#AED581', '#66BB6A',
      ] as any,
      'circle-opacity': [
        'interpolate', ['linear'], ['zoom'],
        GLOBE_MARKERS_MAX_ZOOM - 2, 0.18,
        GLOBE_MARKERS_MAX_ZOOM, 0,
      ] as any,
      'circle-blur': 1,
    },
  })

  // Inner solid circle
  mapInstance.addLayer({
    id: 'city-markers-circle',
    type: 'circle',
    source: 'city-markers',
    maxzoom: GLOBE_MARKERS_MAX_ZOOM,
    paint: {
      'circle-radius': [
        'interpolate', ['linear'], ['zoom'],
        2, 5,
        GLOBE_MARKERS_MAX_ZOOM - 1, 10,
      ] as any,
      'circle-color': [
        'case', ['==', ['get', 'selected'], 1], '#AED581', '#66BB6A',
      ] as any,
      'circle-opacity': [
        'interpolate', ['linear'], ['zoom'],
        GLOBE_MARKERS_MAX_ZOOM - 2, 0.92,
        GLOBE_MARKERS_MAX_ZOOM, 0,
      ] as any,
      'circle-stroke-width': 1.5,
      'circle-stroke-color': 'rgba(255, 255, 255, 0.85)',
      'circle-stroke-opacity': [
        'interpolate', ['linear'], ['zoom'],
        GLOBE_MARKERS_MAX_ZOOM - 2, 0.85,
        GLOBE_MARKERS_MAX_ZOOM, 0,
      ] as any,
    },
  })

  // City name label
  mapInstance.addLayer({
    id: 'city-markers-label',
    type: 'symbol',
    source: 'city-markers',
    maxzoom: GLOBE_MARKERS_MAX_ZOOM,
    layout: {
      'text-field': ['get', 'name'] as any,
      'text-font': ['Noto Sans Bold', 'Noto Sans Regular'],
      'text-size': [
        'interpolate', ['linear'], ['zoom'],
        2, 10,
        GLOBE_MARKERS_MAX_ZOOM - 1, 13,
      ] as any,
      'text-offset': [0, 1.3],
      'text-anchor': 'top',
      'text-allow-overlap': false,
    },
    paint: {
      'text-color': '#c8e6c9',
      'text-halo-color': 'rgba(0, 0, 0, 0.85)',
      'text-halo-width': 1.5,
      'text-opacity': [
        'interpolate', ['linear'], ['zoom'],
        GLOBE_MARKERS_MAX_ZOOM - 2, 1,
        GLOBE_MARKERS_MAX_ZOOM, 0,
      ] as any,
    },
  })
}

export function updateCityMarkersSelected(mapInstance: maplibregl.Map, selectedCity: CityCode): void {
  const src = mapInstance.getSource('city-markers') as maplibregl.GeoJSONSource | undefined
  if (!src) return
  src.setData(cityMarkersGeoJSON(selectedCity))
}

export function removeCityMarkers(mapInstance: maplibregl.Map): void {
  if (mapInstance.getLayer('city-markers-label')) mapInstance.removeLayer('city-markers-label')
  if (mapInstance.getLayer('city-markers-circle')) mapInstance.removeLayer('city-markers-circle')
  if (mapInstance.getLayer('city-markers-glow')) mapInstance.removeLayer('city-markers-glow')
  if (mapInstance.getSource('city-markers')) mapInstance.removeSource('city-markers')
}

export function bindCityMarkerInteractions(
  mapInstance: maplibregl.Map,
  onCityClick: (code: CityCode) => void,
): void {
  const interactiveLayers = ['city-markers-circle', 'city-markers-glow']

  mapInstance.on('click', (e) => {
    const features = mapInstance.queryRenderedFeatures(e.point, { layers: interactiveLayers })
    if (!features.length) return
    const code = features[0].properties?.code as string | undefined
    if (code && code in CITY_CONFIG) onCityClick(code as CityCode)
  })

  for (const layer of interactiveLayers) {
    mapInstance.on('mouseenter', layer, () => { mapInstance.getCanvas().style.cursor = 'pointer' })
    mapInstance.on('mouseleave', layer, () => { mapInstance.getCanvas().style.cursor = '' })
  }
}
