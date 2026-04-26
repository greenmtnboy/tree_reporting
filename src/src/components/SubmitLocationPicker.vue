<template>
  <div class="location-picker">
    <div ref="container" class="location-picker__map"></div>
    <button type="button" class="location-picker__recenter" @click="recenter">
      Recenter
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import maplibregl from 'maplibre-gl'

const props = defineProps<{
  lat: number
  lng: number
  userLat: number
  userLng: number
  zoom?: number
  maxZoom?: number
}>()
const emit = defineEmits<{
  (e: 'update', payload: { lat: number; lng: number; source: 'drag' | 'recenter' }): void
}>()

const container = ref<HTMLDivElement | null>(null)
let map: maplibregl.Map | null = null
let marker: maplibregl.Marker | null = null
let userMarker: maplibregl.Marker | null = null

function makeUserDotEl(): HTMLDivElement {
  const el = document.createElement('div')
  el.className = 'location-picker__user-dot'
  const pulse = document.createElement('span')
  pulse.className = 'location-picker__user-pulse'
  const core = document.createElement('span')
  core.className = 'location-picker__user-core'
  el.appendChild(pulse)
  el.appendChild(core)
  return el
}

onMounted(() => {
  if (!container.value) return
  map = new maplibregl.Map({
    container: container.value,
    style: 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json',
    center: [props.lng, props.lat],
    zoom: props.zoom ?? 19,
    maxZoom: props.maxZoom ?? 21,
    attributionControl: false,
  })
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
  map.on('load', () => {
    if (!map) return
    userMarker = new maplibregl.Marker({ element: makeUserDotEl() })
      .setLngLat([props.userLng, props.userLat])
      .addTo(map)
    marker = new maplibregl.Marker({ draggable: true, color: '#A7E3B2' })
      .setLngLat([props.lng, props.lat])
      .addTo(map)
    marker.on('dragend', () => {
      if (!marker) return
      const { lng, lat } = marker.getLngLat()
      emit('update', { lat, lng, source: 'drag' })
    })
  })
})

watch(
  () => [props.lat, props.lng],
  ([lat, lng]) => {
    if (marker) marker.setLngLat([lng, lat])
  },
)

watch(
  () => [props.userLat, props.userLng],
  ([lat, lng]) => {
    if (userMarker) userMarker.setLngLat([lng, lat])
  },
)

function recenter() {
  if (!map) return
  map.flyTo({
    center: [props.userLng, props.userLat],
    zoom: Math.max(map.getZoom(), props.zoom ?? 19),
  })
  if (marker) {
    marker.setLngLat([props.userLng, props.userLat])
    emit('update', { lat: props.userLat, lng: props.userLng, source: 'recenter' })
  }
}

onBeforeUnmount(() => {
  marker?.remove()
  marker = null
  userMarker?.remove()
  userMarker = null
  map?.remove()
  map = null
})
</script>

<style scoped>
.location-picker {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 320px;
}

.location-picker__map {
  width: 100%;
  height: 100%;
}

.location-picker__recenter {
  position: absolute;
  left: 12px;
  bottom: 12px;
  padding: 8px 14px;
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: rgba(18, 22, 28, 0.85);
  color: var(--color-ink);
  border: 1px solid rgba(167, 227, 178, 0.3);
  cursor: pointer;
  z-index: 2;
}

.location-picker__recenter:hover {
  background: rgba(47, 125, 79, 0.28);
}
</style>

<style>
.location-picker__user-dot {
  position: relative;
  width: 18px;
  height: 18px;
  pointer-events: none;
}

.location-picker__user-core {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 12px;
  height: 12px;
  margin-left: -6px;
  margin-top: -6px;
  border-radius: 50%;
  background: #4a9eff;
  border: 2px solid #ffffff;
  box-shadow: 0 0 6px rgba(10, 20, 40, 0.55);
}

.location-picker__user-pulse {
  position: absolute;
  left: 50%;
  top: 50%;
  width: 14px;
  height: 14px;
  margin-left: -7px;
  margin-top: -7px;
  border-radius: 50%;
  background: rgba(74, 158, 255, 0.45);
  animation: location-picker-pulse 1.8s ease-out infinite;
}

@keyframes location-picker-pulse {
  0% {
    transform: scale(0.6);
    opacity: 0.7;
  }
  100% {
    transform: scale(3.4);
    opacity: 0;
  }
}
</style>
