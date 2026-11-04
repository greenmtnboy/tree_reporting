import type { TreePosition } from './streetView'

export interface Panorama {
  getPosition(): { lat(): number; lng(): number } | null
  getPov(): { heading: number; pitch: number }
  getStatus(): string
  setPosition(position: TreePosition): void
  setVisible(visible: boolean): void
  addListener(event: string, callback: () => void): { remove(): void }
}
export interface StreetViewMaps {
  StreetViewPanorama: new (container: HTMLElement, options: Record<string, unknown>) => Panorama
  event: { trigger(instance: Panorama, event: string): void; clearInstanceListeners(instance: Panorama): void }
}
let loading: Promise<StreetViewMaps> | undefined

/** Load once, only after a visitor opens Street View; failed loads can be retried. */
export function loadStreetView(key: string): Promise<StreetViewMaps> {
  if (loading) return loading
  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const globals = window as unknown as { google?: { maps: StreetViewMaps }; treeStreetViewReady?: () => void }
    const timer = window.setTimeout(() => fail(), 20000)
    function fail() {
      clearTimeout(timer)
      script.remove()
      delete globals.treeStreetViewReady
      loading = undefined
      reject(new Error('Street View could not load. Try Google Maps below.'))
    }
    globals.treeStreetViewReady = () => {
      clearTimeout(timer)
      delete globals.treeStreetViewReady
      if (globals.google?.maps) resolve(globals.google.maps)
      else fail()
    }
    script.src = `https://maps.googleapis.com/maps/api/js?${new URLSearchParams({ key, callback: 'treeStreetViewReady', loading: 'async', v: 'weekly' })}`
    script.async = true
    script.onerror = fail
    document.head.append(script)
  })
  return loading
}
