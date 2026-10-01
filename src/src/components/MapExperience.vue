<template>
  <section v-if="showEntry" class="map-entry" aria-labelledby="map-entry-title">
    <div class="map-entry-card">
      <span class="eyebrow">Urban Trees</span>
      <h1 id="map-entry-title">Discover the trees around you.</h1>
      <p>Get to know your neighborhood, or explore an entire city.</p>
      <div class="entry-actions">
        <button class="floating-action floating-action--primary entry-action" :disabled="locating" :aria-busy="locating" :aria-describedby="locating || locationError ? 'entry-feedback' : 'nearby-help'" @click="chooseNearby">
          <svg class="entry-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true">
            <circle cx="12" cy="12" r="7" /><circle cx="12" cy="12" r="2" />
            <path d="M12 2v3m0 14v3M2 12h3m14 0h3" />
          </svg>
          {{ locationError ? 'Try location again' : 'Near Me' }}
        </button>
        <button class="floating-action entry-action" :aria-describedby="locating || locationError ? undefined : 'explore-help'" @click="chooseExplore">
          <svg class="entry-action-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" aria-hidden="true">
            <path d="m3 5 6-2 6 2 6-2v16l-6 2-6-2-6 2V5Zm6-2v16m6-14v16" />
          </svg>
          Explore City
        </button>
      </div>
      <div id="entry-feedback" class="entry-feedback">
        <p v-if="locating" role="status">Finding your location… Allow access if your browser asks.</p>
        <p v-else-if="locationError" class="entry-error" role="alert">{{ locationError }}</p>
        <template v-else>
          <p id="nearby-help">Near Me uses your location to find local trees.</p>
          <p id="explore-help">Explore City works without location access.</p>
        </template>
      </div>
    </div>
  </section>
  <TreeMap v-else :key="mode ?? undefined" :simplified="simplified || mode === 'nearby'" :nearby-location="mode === 'nearby' ? location : null" />
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TreeMap from './TreeMap.vue'
import { cityAt, useMapData } from '../composables/useMapData'
import { getCurrentPosition } from '../lib/geo'

defineProps<{ simplified?: boolean }>()
const emit = defineEmits<{ 'entry-change': [visible: boolean] }>()
const route = useRoute()
const router = useRouter()
const { displayCity, setUserLocation } = useMapData()
// City is a destination hint, never an implicit experience selection.
const mode = computed(() => {
  if (route.query.mode === 'nearby') return 'nearby'
  return route.query.mode === 'explore' || route.query.tree ? 'explore' : null
})
const location = ref<{ lat: number; lng: number } | null>(null)
const showEntry = computed(() => !mode.value || (mode.value === 'nearby' && !location.value))
watch(showEntry, value => emit('entry-change', value), { immediate: true })
const locating = ref(false)
const locationError = ref('')
let request = 0

async function locate() {
  const id = ++request
  locating.value = true
  locationError.value = ''
  performance.mark('trees:location:start')
  try {
    const fix = await getCurrentPosition({ enableHighAccuracy: false, maximumAge: 60_000, timeout: 10_000 })
    if (id !== request) return
    const position = { lat: fix.coords.latitude, lng: fix.coords.longitude }
    const city = cityAt(position.lat, position.lng)
    if (!city) throw new Error('We do not have a tree inventory near you yet. Choose Explore City to visit a mapped city.')
    await router.replace({ query: { ...route.query, city, mode: 'nearby', tree: undefined } })
    if (id !== request) return
    setUserLocation(position.lat, position.lng, fix.coords.accuracy)
    location.value = position
    performance.mark('trees:location:ready')
    performance.measure('trees:location', 'trees:location:start', 'trees:location:ready')
  } catch (error) {
    if (id !== request) return
    const code = (error as { code?: number }).code
    locationError.value = code === 1 ? 'Location access is off. Allow location in your browser or choose Explore City.'
      : code === 3 ? 'Finding your location took too long. Try again or choose Explore City.'
        : code === 2 ? 'Your location is unavailable. Try again or choose Explore City.'
          : (error as Error).message
  } finally {
    if (id === request) locating.value = false
  }
}

function chooseNearby() {
  if (mode.value === 'nearby') void locate()
  else void router.push({ query: { ...route.query, mode: 'nearby', tree: undefined } })
}
function chooseExplore() {
  void router.push({ query: { ...route.query, mode: 'explore', city: displayCity.value } })
}
watch(mode, (value) => {
  request++
  locating.value = false
  location.value = null
  locationError.value = ''
  if (value === 'nearby') {
    // A shared tree identifies a destination. Opening it must not redirect to
    // the recipient's city; selecting a tree on an already-open map is unchanged.
    if (route.query.tree) void router.replace({ query: { ...route.query, mode: 'explore' } })
    else void locate()
  } else if (value === 'explore' && route.query.tree && route.query.mode !== 'explore') {
    // Keep exploration selected after a shared tree card is closed (or missing).
    void router.replace({ query: { ...route.query, mode: 'explore' } })
  }
}, { immediate: true })
onUnmounted(() => { request++ })
</script>

<style scoped>
.map-entry {
  height: 100%; overflow-y: auto; display: grid; place-items: center;
  padding: max(28px, env(safe-area-inset-top, 0px)) 24px max(28px, env(safe-area-inset-bottom, 0px));
  background: radial-gradient(ellipse at top, rgba(var(--accent-rgb), .1), transparent 75%);
}
.map-entry-card { width: 100%; max-width: 380px; text-align: center; color: var(--color-ink); }
.eyebrow { color: var(--color-leaf); font-weight: 700; letter-spacing: .16em; text-transform: uppercase; font-size: .75rem; }
h1 { font-family: var(--font-display); font-weight: 400; font-size: clamp(2rem, 7vw, 3rem); letter-spacing: -.035em; line-height: 1.1; margin: 16px 0; text-wrap: balance; }
p { line-height: 1.6; color: var(--color-muted); }
.entry-actions { display: grid; gap: 12px; margin: 28px 0 20px; }
.entry-action { min-height: 60px; font-size: 1rem; }
.entry-action-icon { width: 24px; height: 24px; flex-shrink: 0; }
.entry-feedback { min-height: 4.8em; font-size: .8rem; }
.entry-error { color: var(--color-error); }
@media (max-width: 768px) {
  .map-entry { padding-bottom: calc(88px + env(safe-area-inset-bottom, 0px)); }
}
@media (max-width: 768px) and (max-height: 500px) {
  .map-entry { padding-top: 16px; padding-bottom: calc(76px + env(safe-area-inset-bottom, 0px)); }
  .map-entry-card { max-width: 500px; }
  h1 { font-size: 2rem; margin: 10px 0; }
  p { font-size: .875rem; }
  .entry-actions { grid-template-columns: 1fr 1fr; margin: 16px 0 12px; }
  .entry-action { min-height: 52px; }
  .entry-feedback { min-height: 0; }
  .entry-feedback p { font-size: .8rem; }
}
</style>
