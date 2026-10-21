<template>
  <Teleport to="body">
    <dialog ref="dialog" class="street-dialog" aria-labelledby="street-title" @close="emit('close')" @click="onBackdrop">
      <header>
        <h2 id="street-title">Street View near this tree</h2>
        <button type="button" aria-label="Close Street View" @click="dialog?.close()">✕</button>
      </header>
      <p v-if="!embedUrl" role="status">Street View is not configured yet.</p>
      <div v-else class="street-content">
        <iframe
          :src="embedUrl"
          class="street-panorama"
          title="Google Street View near this tree"
          referrerpolicy="strict-origin-when-cross-origin"
          allowfullscreen
        ></iframe>
        <aside class="street-location" aria-label="Tree location">
          <div ref="mapEl" class="street-minimap" aria-label="Map of the selected tree location"></div>
          <p>Tree location · does not track Street View</p>
        </aside>
      </div>
      <footer>
        <span v-if="embedUrl">Look around to find the tree. Imagery may predate it. If the view is unavailable, try Google Maps.</span>
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
import { streetViewEmbedUrl, streetViewExternalUrl, type TreePosition } from '../lib/streetView'

const props = defineProps<{ position: TreePosition }>()
const emit = defineEmits<{ close: [] }>()
const dialog = ref<HTMLDialogElement>()
const mapEl = ref<HTMLDivElement>()
const embedUrl = computed(() => streetViewEmbedUrl(props.position, import.meta.env.VITE_GOOGLE_MAPS_API_KEY))
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
onMounted(() => {
  dialog.value?.showModal()
  if (!mapEl.value) return
  const center: [number, number] = [props.position.lng, props.position.lat]
  miniMap = new maplibregl.Map({ container: mapEl.value, style: basemapStyleUrl(resolvedTheme.value),
    center, zoom: 17, interactive: false, attributionControl: { compact: true } })
  unbindTheme = bindMapTheme(miniMap)
  marker = new maplibregl.Marker({ color: '#31734d' }).setLngLat(center).addTo(miniMap)
})
watch(() => [props.position.lng, props.position.lat], ([lng, lat]) => {
  miniMap?.jumpTo({ center: [lng, lat] })
  marker?.setLngLat([lng, lat])
})
onUnmounted(() => {
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
header button { font-size: 20px; background: none; border: none; color: inherit; }
p { padding: 20px; }
.street-content { display: grid; grid-template-columns: minmax(200px, 1fr) 240px; gap: 12px; padding: 0 12px; }
.street-panorama { width: 100%; height: min(62dvh, 560px); min-height: 260px; border: 0; display: block; }
.street-minimap { width: 100%; height: 220px; border-radius: 6px; overflow: hidden; }
.street-location p { padding: 8px 0; margin: 0; font-size: 12px; line-height: 1.4; }
footer { font-size: 12px; flex-wrap: wrap; }
footer span { flex: 1; min-width: 180px; }
@media (max-width: 600px) { .street-content { grid-template-columns: minmax(200px, 1fr); } .street-minimap { height: 160px; } .street-panorama { height: 42dvh; } }
</style>
