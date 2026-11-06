<template>
  <Teleport to="body">
    <dialog ref="dialog" class="street-dialog" :class="{ expanded }" aria-labelledby="street-title" @close="emit('close')" @click="onBackdrop">
      <header>
        <h2 id="street-title">Street View near this tree</h2>
        <button type="button" :aria-pressed="expanded" @click="expanded = !expanded">{{ expanded ? 'Restore view' : 'Full screen' }}</button>
        <button type="button" aria-label="Close Street View" @click="dialog?.close()">✕</button>
      </header>
      <div class="street-content">
        <section class="street-scene">
          <p v-if="message" role="status">{{ message }}</p>
          <div ref="panoramaEl" class="street-panorama" aria-label="Google Street View near this tree"></div>
        </section>
        <aside class="street-location" aria-label="Tree location and corrections">
          <div ref="mapEl" class="street-minimap" aria-label="Map of tree and Street View camera"></div>
          <p>Green: tree · Blue arrow: camera and viewing direction</p>
          <p v-if="!cameraAvailable">Camera location unavailable</p>
          <slot name="feedback" />
        </aside>
      </div>
      <footer>
        <span>Look around to find the tree. Imagery may predate it. If the view is unavailable, try Google Maps.</span>
        <a :href="externalUrl" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>
      </footer>
    </dialog>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import maplibregl from 'maplibre-gl'
import { basemapStyleUrl, bindMapTheme } from '../composables/mapTheme'
import { useTheme } from '../composables/useTheme'
import { streetViewExternalUrl, type TreePosition } from '../lib/streetView'

import { loadStreetView, type Panorama, type StreetViewMaps } from '../lib/streetViewRuntime'

const props = defineProps<{ position: TreePosition }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const mapEl = ref<HTMLDivElement>()
const panoramaEl = ref<HTMLDivElement>()
const expanded = ref(false)
const cameraAvailable = ref(false)
const message = ref('Loading Street View…')
let panorama: Panorama | undefined
let maps: StreetViewMaps | undefined
let cameraMarker: maplibregl.Marker | undefined
let observer: ResizeObserver | undefined
let disposed = false
function syncCamera(fit = true) {
  const position = panorama?.getPosition()
  cameraAvailable.value = !!position && panorama?.getStatus() === 'OK'
  if (!cameraAvailable.value || !position || !miniMap) { cameraMarker?.remove(); return }
  if (!cameraMarker) {
    const arrow = document.createElement('div')
    arrow.textContent = '▲'
    arrow.style.cssText = 'color:#1674db;font-size:32px;text-shadow:0 0 3px white;'
    arrow.setAttribute('aria-label', 'Street View camera')
    cameraMarker = new maplibregl.Marker({ element: arrow, rotationAlignment: 'map' })
  }
  cameraMarker.setLngLat([position.lng(), position.lat()]).setRotation(panorama!.getPov().heading).addTo(miniMap)
  if (fit) miniMap.fitBounds([[props.position.lng, props.position.lat], [position.lng(), position.lat()]], { padding: 45, maxZoom: 18, duration: 0 })
}
const externalUrl = computed(() => streetViewExternalUrl(props.position))
const { resolvedTheme } = useTheme()
let miniMap: maplibregl.Map | undefined
let marker: maplibregl.Marker | undefined
let unbindTheme: (() => void) | undefined
const previousFocus = document.activeElement as HTMLElement | null
function onBackdrop(event: MouseEvent) {
  if (event.target !== dialog.value) return
  const rect = dialog.value!.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.value?.close()
}
onMounted(async () => {
  dialog.value?.showModal()
  if (!mapEl.value) return
  const center: [number, number] = [props.position.lng, props.position.lat]
  miniMap = new maplibregl.Map({ container: mapEl.value, style: basemapStyleUrl(resolvedTheme.value),
    center, zoom: 17, interactive: true, attributionControl: { compact: true } })
  unbindTheme = bindMapTheme(miniMap)
  marker = new maplibregl.Marker({ color: '#31734d' }).setLngLat(center).addTo(miniMap)
  observer = new ResizeObserver(() => {
    miniMap?.resize()
    if (panorama && maps) maps.event.trigger(panorama, 'resize')
  })
  observer.observe(dialog.value!)
  const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY?.trim()
  if (!key) { message.value = 'Street View is not configured yet.'; return }
  try {
    maps = await loadStreetView(key)
    if (disposed) return
    panorama = new maps.StreetViewPanorama(panoramaEl.value!, {
      position: props.position, pov: { heading: 0, pitch: 0 }, zoom: 1,
      fullscreenControl: false, addressControl: false, motionTracking: false, motionTrackingControl: false,
    })
    panorama.addListener('position_changed', syncCamera)
    panorama.addListener('pov_changed', () => syncCamera(false))
    panorama.addListener('status_changed', () => {
      message.value = panorama?.getStatus() === 'OK' ? '' : 'Street View is unavailable here. Try Google Maps below.'
      syncCamera()
    })
    if (panorama.getStatus() === 'OK') message.value = ''
    syncCamera()
  } catch { if (!disposed) message.value = 'Street View could not load. Try Google Maps below.' }
})
watch(() => [props.position.lng, props.position.lat], ([lng, lat]) => {
  miniMap?.jumpTo({ center: [lng, lat] })
  marker?.setLngLat([lng, lat])
  panorama?.setPosition({ lng, lat })
})
onUnmounted(() => {
  disposed = true
  observer?.disconnect()
  if (panorama) { maps?.event.clearInstanceListeners(panorama); panorama.setVisible(false) }
  cameraMarker?.remove()
  unbindTheme?.()
  marker?.remove()
  miniMap?.remove()
  previousFocus?.focus()
})
</script>

<style scoped>
.street-dialog { position: fixed; inset: 0; margin: auto; overflow: auto; width: min(1000px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 0; border: 1px solid rgba(var(--ink-rgb), .2); border-radius: 12px; background: rgb(var(--surface-raised-rgb)); color: rgb(var(--ink-rgb)); }
.street-dialog::backdrop { background: #0009; }
header, footer { display: flex; align-items: center; gap: 16px; padding: 14px 18px; }
header { justify-content: space-between; }
h2 { font-size: 18px; margin: 0; }
button, a { cursor: pointer; }
footer a { color: var(--color-leaf); }
:slotted(button) { min-height: 44px; padding: 8px 12px; margin: 4px 4px 4px 0; border: 1px solid rgba(var(--ink-rgb), .3); border-radius: 6px; background: rgb(var(--surface-raised-rgb)); color: inherit; cursor: pointer; }
header button { font-size: 14px; min-height: 44px; background: none; border: none; color: inherit; }
p { padding: 20px; }
.street-content { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 12px; padding: 0 12px; }
.street-panorama { width: 100%; height: min(62dvh, 560px); min-height: 260px; border: 0; display: block; }
.street-minimap { width: 100%; height: 220px; border-radius: 6px; overflow: hidden; }
.street-location p { padding: 8px 0; margin: 0; font-size: 12px; line-height: 1.4; }
footer { font-size: 12px; flex-wrap: wrap; }
footer span { flex: 1; min-width: 180px; }
@media (max-width: 600px) { .street-content { grid-template-columns: minmax(200px, 1fr); } .street-minimap { height: 160px; } .street-panorama { height: 42dvh; } }
.street-dialog.expanded { width: 100vw; max-width: 100vw; height: 100dvh; max-height: 100dvh; border-radius: 0; }
.street-dialog.expanded .street-content { min-height: calc(100dvh - 150px); }
.street-dialog.expanded .street-panorama { height: calc(100dvh - 150px); }
.street-location { min-width: 0; }
.street-scene { position: sticky; top: 0; align-self: start; min-width: 0; }
.street-scene > p { position: absolute; z-index: 1; background: rgb(var(--surface-raised-rgb)); }
@media (max-width: 700px) {
  .street-content { grid-template-columns: minmax(0, 1fr); }
  .street-scene { position: relative; }
  .street-dialog.expanded .street-panorama { height: 45dvh; }
  header { gap: 8px; padding: 10px; }
  h2 { font-size: 15px; }
}
</style>
