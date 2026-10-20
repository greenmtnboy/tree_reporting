export interface TreePosition { lat: number; lng: number }

/** Clockwise bearing from true north, including across the antimeridian. */
export function headingToTree(from: TreePosition, to: TreePosition): number {
  const rad = Math.PI / 180
  const delta = (to.lng - from.lng) * rad
  const y = Math.sin(delta) * Math.cos(to.lat * rad)
  const x = Math.cos(from.lat * rad) * Math.sin(to.lat * rad)
    - Math.sin(from.lat * rad) * Math.cos(to.lat * rad) * Math.cos(delta)
  return (Math.atan2(y, x) / rad + 360) % 360
}

let loading: Promise<typeof google.maps> | undefined
export function loadStreetView(): Promise<typeof google.maps> {
  if (loading) return loading
  const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY
  if (!key) return Promise.reject(new Error('Street View is not configured yet.'))
  loading = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    const scope = window as typeof window & { arboraryMapsReady?: () => void }
    const timer = window.setTimeout(() => fail(), 20000)
    const cleanup = () => { window.clearTimeout(timer); delete scope.arboraryMapsReady }
    const fail = () => {
      cleanup()
      script.remove()
      reject(new Error('Street View could not load. Please try again.'))
    }
    scope.arboraryMapsReady = () => { cleanup(); resolve(google.maps) }
    script.onerror = fail
    script.async = true
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}&v=quarterly&loading=async&callback=arboraryMapsReady`
    document.head.appendChild(script)
  })
  loading.catch(() => { loading = undefined })
  return loading
}
