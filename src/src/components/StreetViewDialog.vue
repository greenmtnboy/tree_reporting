<template>
  <Teleport to="body">
    <dialog ref="dialog" class="street-dialog" aria-labelledby="street-title" @close="emit('close')" @click="onBackdrop">
      <header>
        <h2 id="street-title">Street View near this tree</h2>
        <button type="button" aria-label="Close Street View" @click="dialog?.close()">✕</button>
      </header>
      <p v-if="message" role="status">{{ message }}</p>
      <div v-show="ready" class="street-content">
        <div ref="panoramaEl" class="street-panorama" aria-label="Street View panorama"></div>
        <div ref="mapEl" class="street-minimap" aria-label="Map showing the tree and camera direction"></div>
      </div>
      <footer>
        <span v-if="ready">Green dot: tree · Blue arrow: camera and viewing direction. Imagery may predate this tree.</span>
        <button v-if="ready" type="button" @click="faceTree">Face tree</button>
        <a :href="externalUrl" target="_blank" rel="noopener noreferrer">Open in Google Maps</a>
      </footer>
    </dialog>
  </Teleport>
</template>

<script setup lang="ts">
/* global google */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { headingToTree, loadStreetView, type TreePosition } from '../lib/streetView'

const props = defineProps<{ position: TreePosition }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const panoramaEl = ref<HTMLDivElement>()
const mapEl = ref<HTMLDivElement>()
const ready = ref(false)
const message = ref('Finding the nearest outdoor Street View…')
const externalUrl = computed(() => `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${props.position.lat},${props.position.lng}`)
let disposed = false
let panorama: google.maps.StreetViewPanorama | undefined
let miniMap: google.maps.Map | undefined
let camera: google.maps.Marker | undefined
let tree: google.maps.Marker | undefined
let line: google.maps.Polyline | undefined
const listeners: google.maps.MapsEventListener[] = []
const previousFocus = document.activeElement as HTMLElement | null
function onBackdrop(event: MouseEvent) {
  if (event.target !== dialog.value) return
  const rect = dialog.value!.getBoundingClientRect()
  if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.value?.close()
}
function faceTree() {
  const position = panorama?.getPosition()?.toJSON()
  if (position) panorama!.setPov({ heading: headingToTree(position, props.position), pitch: 0 })
}
function syncCamera(fit: boolean) {
  const position = panorama?.getPosition()
  if (!position || !miniMap || !camera) return
  camera.setPosition(position)
  camera.setIcon({ path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW, scale: 6,
    rotation: panorama!.getPov().heading, fillColor: '#2563eb', fillOpacity: 1, strokeColor: '#fff', strokeWeight: 2 })
  line?.setPath([position, props.position])
  if (fit) {
    const bounds = new google.maps.LatLngBounds().extend(position).extend(props.position)
    miniMap.fitBounds(bounds, 45)
  }
}
onMounted(async () => {
  dialog.value?.showModal()
  try {
    const maps = await loadStreetView()
    if (disposed) return
    const result = await new maps.StreetViewService().getPanorama({
      location: props.position, radius: 100, preference: maps.StreetViewPreference.NEAREST,
      sources: [maps.StreetViewSource.OUTDOOR],
    })
    if (disposed) return
    const location = result.data.location
    if (!location?.latLng || !location.pano) throw new Error('No outdoor Street View was found within 100 metres of this tree.')
    ready.value = true
    message.value = ''
    await nextTick()
    if (disposed) return
    panorama = new maps.StreetViewPanorama(panoramaEl.value!, {
      pano: location.pano,
      pov: { heading: headingToTree(location.latLng.toJSON(), props.position), pitch: 0 },
      motionTracking: false, motionTrackingControl: false, fullscreenControl: false,
    })
    miniMap = new maps.Map(mapEl.value!, { center: props.position, zoom: 18, maxZoom: 20,
      disableDefaultUI: true, zoomControl: true, streetViewControl: false, clickableIcons: false })
    tree = new maps.Marker({ map: miniMap, position: props.position, title: 'Tree',
      icon: { path: maps.SymbolPath.CIRCLE, scale: 7, fillColor: '#31734d', fillOpacity: 1, strokeColor: '#fff', strokeWeight: 2 } })
    camera = new maps.Marker({ map: miniMap, title: 'Street View camera' })
    line = new maps.Polyline({ map: miniMap, strokeColor: '#31734d', strokeOpacity: 0.6, strokeWeight: 2 })
    listeners.push(panorama.addListener('position_changed', () => syncCamera(true)))
    listeners.push(panorama.addListener('pov_changed', () => syncCamera(false)))
    syncCamera(true)
  } catch (error) {
    if (disposed) return
    ready.value = false
    const code = (error as { code?: string })?.code
    message.value = code === 'ZERO_RESULTS'
      ? 'No outdoor Street View was found within 100 metres of this tree.'
      : code ? 'Street View is unavailable right now. Try opening Google Maps.'
        : error instanceof Error ? error.message : 'Street View is unavailable near this tree. Try opening Google Maps.'
  }
})
onUnmounted(() => {
  disposed = true
  listeners.forEach(listener => listener.remove())
  panorama?.setVisible(false)
  camera?.setMap(null)
  tree?.setMap(null)
  line?.setMap(null)
  previousFocus?.focus()
})
</script>

<style scoped>
.street-dialog { width: min(1000px, calc(100vw - 32px)); max-height: calc(100dvh - 32px); padding: 0; border: 1px solid rgba(var(--ink-rgb), .2); border-radius: 12px; background: rgb(var(--surface-raised-rgb)); color: rgb(var(--ink-rgb)); }
.street-dialog::backdrop { background: #0009; }
header, footer { display: flex; align-items: center; gap: 16px; padding: 14px 18px; }
header { justify-content: space-between; }
h2 { font-size: 18px; margin: 0; }
button, a { cursor: pointer; }
footer a { color: var(--color-leaf); }
footer button { padding: 6px 10px; border: 1px solid rgba(var(--ink-rgb), .25); border-radius: 5px; color: inherit; background: transparent; }
header button { font-size: 20px; background: none; border: none; color: inherit; }
p { padding: 20px; }
.street-content { position: relative; }
.street-panorama { height: min(62dvh, 560px); min-height: 260px; }
.street-minimap { position: absolute; bottom: 36px; left: 12px; width: 260px; height: 180px; border: 2px solid white; border-radius: 6px; }
footer { font-size: 12px; flex-wrap: wrap; }
footer span { flex: 1; min-width: 180px; }
@media (max-width: 600px) { .street-minimap { position: relative; inset: auto; width: 100%; height: 160px; border: 0; } .street-panorama { height: 42dvh; } }
</style>
