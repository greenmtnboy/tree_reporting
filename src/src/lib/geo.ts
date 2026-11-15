import { computed, readonly, ref } from 'vue'
import { Capacitor } from '@capacitor/core'
import { Geolocation } from '@capacitor/geolocation'

type PositionFix = {
  timestamp: number
  coords: Pick<GeolocationCoordinates, 'latitude' | 'longitude' | 'accuracy' | 'heading' | 'speed'>
}

const DEFAULT_POSITION_OPTIONS: PositionOptions = {
  enableHighAccuracy: true,
  timeout: 15_000,
  maximumAge: 0,
}

export interface SharedPosition {
  lat: number
  lng: number
  accuracy: number | null
  heading: number | null
  speed: number | null
  timestamp: number
}

const sharedPosition = ref<SharedPosition | null>(null)
const sharedPositionError = ref<string | null>(null)
const sharedPositionWatchActive = ref(false)

let releaseSharedWatch: (() => void) | null = null
let sharedPositionWatchRetainers = 0

function normalizePosition(position: PositionFix): SharedPosition {
  return {
    lat: position.coords.latitude,
    lng: position.coords.longitude,
    accuracy: position.coords.accuracy ?? null,
    heading: position.coords.heading ?? null,
    speed: position.coords.speed ?? null,
    timestamp: position.timestamp,
  }
}

export function geolocationAvailable(): boolean {
  return Capacitor.isNativePlatform() || (typeof navigator !== 'undefined' && !!navigator.geolocation)
}

function geolocationUnavailableError(): Error {
  return new Error('Geolocation is not available in this browser')
}

function sharedPositionOptions(options: PositionOptions = {}): PositionOptions {
  return {
    ...DEFAULT_POSITION_OPTIONS,
    ...options,
  }
}

export function getCurrentPosition(
  options: PositionOptions = {},
): Promise<PositionFix> {
  if (Capacitor.isNativePlatform()) {
    return Geolocation.getCurrentPosition(sharedPositionOptions(options))
  }
  return new Promise((resolve, reject) => {
    if (typeof navigator === 'undefined' || !navigator.geolocation) {
      reject(geolocationUnavailableError())
      return
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, sharedPositionOptions(options))
  })
}

export function watchCurrentPosition(
  onSuccess: (position: PositionFix) => void,
  onError?: (error: { message: string }) => void,
  options: PositionOptions = {},
): () => void {
  if (Capacitor.isNativePlatform()) {
    let stopped = false
    const report = (error: unknown) => {
      if (!stopped) onError?.({ message: error instanceof Error ? error.message : String(error) })
    }
    const started = Geolocation.watchPosition(sharedPositionOptions(options), (position, error) => {
      if (stopped) return
      if (error) report(error)
      else if (position) onSuccess(position)
    })
    void started.catch(report)
    return () => {
      if (stopped) return
      stopped = true
      // The user can leave the map while the OS permission dialog is open.
      void started.then(id => Geolocation.clearWatch({ id })).catch(() => {})
    }
  }
  if (typeof navigator === 'undefined' || !navigator.geolocation) {
    throw geolocationUnavailableError()
  }
  const watchId = navigator.geolocation.watchPosition(
    onSuccess,
    onError,
    sharedPositionOptions(options),
  )
  return () => {
    navigator.geolocation.clearWatch(watchId)
  }
}

export async function getGeolocationPermissionState(): Promise<PermissionState | null> {
  if (Capacitor.isNativePlatform()) {
    try {
      const { location } = await Geolocation.checkPermissions()
      return location === 'prompt-with-rationale' ? 'prompt' : location
    } catch {
      return null // Location services may be disabled system-wide.
    }
  }
  if (typeof navigator === 'undefined' || !navigator.permissions?.query) return null
  try {
    const result = await navigator.permissions.query({ name: 'geolocation' as PermissionName })
    return result.state
  } catch {
    return null
  }
}

export function setSharedPosition(location: Omit<SharedPosition, 'timestamp'> & { timestamp?: number }): void {
  sharedPosition.value = {
    ...location,
    timestamp: location.timestamp ?? Date.now(),
  }
  sharedPositionError.value = null
}

export function clearSharedPosition(): void {
  sharedPosition.value = null
}

export async function refreshSharedPosition(
  options: PositionOptions = {},
): Promise<SharedPosition> {
  const position = await getCurrentPosition(options)
  const next = normalizePosition(position)
  sharedPosition.value = next
  sharedPositionError.value = null
  return next
}

function startSharedPositionWatch(options: PositionOptions = {}): void {
  if (releaseSharedWatch) return
  releaseSharedWatch = watchCurrentPosition(
    (position) => {
      sharedPosition.value = normalizePosition(position)
      sharedPositionError.value = null
    },
    (error) => {
      sharedPositionError.value = error.message || 'Location unavailable'
    },
    options,
  )
  sharedPositionWatchActive.value = true
}

function stopSharedPositionWatch(): void {
  releaseSharedWatch?.()
  releaseSharedWatch = null
  sharedPositionWatchActive.value = false
}

export function acquireSharedPositionWatch(
  options: PositionOptions = {},
): () => void {
  sharedPositionWatchRetainers += 1
  try {
    startSharedPositionWatch(options)
  } catch (error) {
    sharedPositionWatchRetainers = Math.max(0, sharedPositionWatchRetainers - 1)
    throw error
  }

  let released = false
  return () => {
    if (released) return
    released = true
    sharedPositionWatchRetainers = Math.max(0, sharedPositionWatchRetainers - 1)
    if (sharedPositionWatchRetainers === 0) {
      stopSharedPositionWatch()
    }
  }
}

export function useSharedPosition() {
  return {
    sharedPosition: readonly(sharedPosition),
    sharedPositionError: readonly(sharedPositionError),
    sharedPositionWatchActive: readonly(sharedPositionWatchActive),
    hasSharedPosition: computed(() => sharedPosition.value !== null),
  }
}
