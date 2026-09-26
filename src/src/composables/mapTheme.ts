import { watch } from 'vue'
import type { Map, StyleSpecification } from 'maplibre-gl'
import { useTheme, type ColorTheme } from './useTheme'

export function basemapStyleUrl(theme: ColorTheme) {
  const style = theme === 'dark' ? 'dark-matter' : 'positron'
  return `https://basemaps.cartocdn.com/gl/${style}-gl-style/style.json`
}

const styles = new globalThis.Map<ColorTheme, Promise<StyleSpecification>>()
function loadStyle(theme: ColorTheme) {
  let promise = styles.get(theme)
  if (!promise) {
    promise = fetch(basemapStyleUrl(theme)).then(async (response) => {
      if (!response.ok) throw new Error(`Basemap theme: HTTP ${response.status}`)
      return await response.json() as StyleSpecification
    }).catch((error) => {
      styles.delete(theme)
      throw error
    })
    styles.set(theme, promise)
  }
  return promise
}

/** Carto's two styles share layer IDs and vector sources. Update their paint
 * in place so tree tiles, custom icons, interactions and camera stay intact. */
export function applyBasemapPaint(map: Map, style: StyleSpecification) {
  const currentLayers = new globalThis.Map(map.getStyle().layers.map((layer) => [layer.id, layer]))
  for (const layer of style.layers) {
    const current = currentLayers.get(layer.id)
    if (!current || current.type !== layer.type) continue
    const paint = layer.paint ?? {}
    for (const name of new Set([...Object.keys(current.paint ?? {}), ...Object.keys(paint)])) {
      map.setPaintProperty(layer.id, name, (paint as Record<string, unknown>)[name] ?? null)
    }
  }
}

export function bindMapTheme(map: Map) {
  const { resolvedTheme } = useTheme()
  let disposed = false
  let ready = false
  let revision = 0
  const initialTheme = resolvedTheme.value
  async function sync() {
    const request = ++revision
    if (!ready) return
    try {
      const style = await loadStyle(resolvedTheme.value)
      if (!disposed && request === revision) applyBasemapPaint(map, style)
    } catch (error) {
      if (!disposed && request === revision) console.warn('Could not update basemap theme', error)
    }
  }
  const onLoad = () => {
    ready = true
    // Cache only the base style, before application tree/landmark layers are added.
    if (!styles.has(initialTheme)) styles.set(initialTheme, Promise.resolve(map.getStyle()))
    void sync()
  }
  map.on('style.load', onLoad)
  const stop = watch(resolvedTheme, () => { void sync() })
  return () => {
    disposed = true
    stop()
    map.off('style.load', onLoad)
  }
}
