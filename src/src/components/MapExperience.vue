<template>
  <section v-if="!mode || (mode === 'nearby' && !location)" class="map-entry" aria-labelledby="map-entry-title">
    <div class="map-entry-card">
      <span class="eyebrow">Urban Trees</span>
      <h1 id="map-entry-title">Discover the trees around you.</h1>
      <p>Get to know your neighborhood, or explore an entire city.</p>
      <p v-if="locating" role="status">Finding your location…</p>
      <p v-if="locationError" role="alert">{{ locationError }}</p>
      <div class="entry-actions">
        <button :disabled="locating" @click="chooseNearby">{{ locationError ? 'Try location again' : 'Near Me' }}</button>
        <button class="secondary" @click="chooseExplore">Explore City</button>
      </div>
      <small>Your location stays out of the shared link.</small>
    </div>
  </section>
  <template v-else>
    <TreeMap :key="mode" :simplified="simplified || mode === 'nearby'" :nearby-location="mode === 'nearby' ? location : null" />
    <button v-if="mode === 'nearby'" class="experience-switch" @click="chooseExplore">Explore City</button>
    <button v-else class="experience-switch" @click="chooseNearby">Near Me</button>
  </template>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TreeMap from './TreeMap.vue'
import { cityAt, useMapData } from '../composables/useMapData'
import { getCurrentPosition } from '../lib/geo'

defineProps<{ simplified?: boolean }>()
const route = useRoute()
const router = useRouter()
const { displayCity, setUserLocation } = useMapData()
// Existing city/tree links retain their direct entry behavior.
const mode = computed(() => route.query.mode === 'nearby' ? 'nearby'
  : route.query.mode === 'explore' || route.query.city || route.query.tree ? 'explore' : null)
const location = ref<{ lat: number; lng: number } | null>(null)
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
  if (value === 'nearby') {
    // A shared tree identifies a destination. Opening it must not redirect to
    // the recipient's city; selecting a tree on an already-open map is unchanged.
    if (route.query.tree) void router.replace({ query: { ...route.query, mode: 'explore' } })
    else void locate()
  }
}, { immediate: true })
onUnmounted(() => { request++ })
</script>

<style scoped>
.map-entry { height: 100%; overflow-y: auto; display: grid; place-items: center; padding: 28px 22px 100px; box-sizing: border-box; background: radial-gradient(ellipse at top, rgba(var(--accent-rgb), .18), transparent 75%); }
.map-entry-card { max-width: 440px; color: var(--color-ink); }
.eyebrow { color: var(--color-leaf); font-weight: 700; letter-spacing: .16em; text-transform: uppercase; font-size: .75rem; }
h1 { font-family: var(--font-display); font-size: clamp(2rem, 7vw, 3rem); line-height: 1.1; margin: 20px 0; }
p { line-height: 1.6; color: var(--color-muted); }
.entry-actions { display: grid; gap: 12px; margin: 28px 0 20px; }
button { min-height: 48px; border-radius: 12px; padding: 12px 18px; border: 1px solid var(--color-leaf); background: var(--color-leaf); color: var(--color-on-accent); font: inherit; font-weight: 700; cursor: pointer; }
button:focus-visible { outline: 3px solid var(--color-ink); outline-offset: 4px; }
button:disabled { opacity: .6; cursor: wait; }
.secondary { background: var(--surface-1); color: var(--color-ink); border-color: var(--color-border); }
small { color: var(--color-muted); }
.experience-switch { position: absolute; left: 12px; top: 72px; z-index: 5; background: var(--surface-1); color: var(--color-ink); font-size: .78rem; min-height: 44px; }
</style>
