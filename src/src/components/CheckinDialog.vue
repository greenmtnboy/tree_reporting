<template>
  <div class="checkin-dialog" role="dialog" aria-modal="true" @click.self="handleDismiss">
    <div class="checkin-dialog__panel">
      <header class="checkin-dialog__header">
        <h2 class="checkin-dialog__title">Check in</h2>
        <button
          type="button"
          class="checkin-dialog__close"
          aria-label="Close"
          @click="handleDismiss"
        >✕</button>
      </header>

      <div class="checkin-dialog__body">
        <p class="checkin-dialog__tree">
          <span class="muted">Tree</span>
          <code>{{ treeId }}</code>
        </p>

        <!-- Locating -->
        <section v-if="state === 'locating'" class="section">
          <p>Finding your location…</p>
        </section>

        <!-- Too far -->
        <section v-else-if="state === 'too-far'" class="section">
          <p class="error-text">You're too far from this tree.</p>
          <p class="muted">
            You're about {{ formatMeters(distance) }} away. Check-ins require being within
            {{ MAX_DISTANCE_M }} meters.
          </p>
          <div class="actions">
            <button type="button" class="btn-secondary" @click="handleDismiss">Cancel</button>
            <button type="button" class="btn-primary" @click="startLocation">Try again</button>
          </div>
        </section>

        <!-- Location error -->
        <section v-else-if="state === 'location-error'" class="section">
          <p class="error-text">Couldn't get your location.</p>
          <p class="muted">{{ locationError }}</p>
          <div class="actions">
            <button type="button" class="btn-secondary" @click="handleDismiss">Cancel</button>
            <button type="button" class="btn-primary" @click="startLocation">Retry</button>
          </div>
        </section>

        <!-- Ready -->
        <section v-else-if="state === 'ready'" class="section">
          <p class="ok-text">You're here — {{ formatMeters(distance) }} from the tree.</p>

          <label class="photo-field">
            <span class="field-label">Photo (optional)</span>
            <input
              ref="photoInputRef"
              type="file"
              accept="image/*"
              capture="environment"
              class="photo-input"
              @change="handlePhotoPicked"
            />
            <span v-if="!photoPreview" class="photo-drop">
              <span aria-hidden="true">📷</span>
              <span>Attach a photo of this tree</span>
            </span>
            <span v-else class="photo-preview-wrap">
              <img :src="photoPreview" alt="Photo preview" class="photo-preview" />
              <span class="muted">Tap to replace</span>
            </span>
          </label>
          <p v-if="photoError" class="error-text">{{ photoError }}</p>

          <div class="actions">
            <button type="button" class="btn-secondary" @click="handleDismiss">Cancel</button>
            <button type="button" class="btn-primary" @click="handleSubmit">Check in</button>
          </div>
        </section>

        <!-- Uploading -->
        <section v-else-if="state === 'uploading'" class="section">
          <p>{{ photoBlob ? 'Uploading photo…' : 'Recording check-in…' }}</p>
          <div v-if="photoBlob" class="progress">
            <div class="progress__bar" :style="{ width: `${Math.round(progress * 100)}%` }"></div>
          </div>
        </section>

        <!-- Done -->
        <section v-else-if="state === 'done'" class="section">
          <p class="ok-text"><strong>Checked in!</strong></p>
          <div class="actions">
            <button type="button" class="btn-primary" @click="handleDismiss">Close</button>
          </div>
        </section>

        <!-- Error -->
        <section v-else-if="state === 'error'" class="section">
          <p class="error-text"><strong>Check-in failed.</strong></p>
          <p class="muted">{{ submitError }}</p>
          <div class="actions">
            <button type="button" class="btn-secondary" @click="handleDismiss">Close</button>
            <button type="button" class="btn-primary" @click="handleSubmit">Retry</button>
          </div>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from 'vue'
import { getCurrentPosition } from '../lib/geo'
import { resizeImage } from '../lib/image'
import { recordCheckin } from '../composables/useSubmissions'
import { haversineKm, closestCityTo } from '../composables/useMapData'
import { useDuckDB } from '../composables/useDuckDB'

const props = defineProps<{
  treeId: string
  treeLat: number
  treeLng: number
  species?: string | null
  treeForm?: string | null
  dbhInches?: number | null
  plantYear?: number | null
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'success'): void
}>()

type State =
  | 'locating'
  | 'too-far'
  | 'location-error'
  | 'ready'
  | 'uploading'
  | 'done'
  | 'error'

const MAX_DISTANCE_M = 50

const state = ref<State>('locating')
const userLat = ref<number | null>(null)
const userLng = ref<number | null>(null)
const distance = ref<number>(0)
const locationError = ref<string>('')
const photoBlob = ref<Blob | null>(null)
const photoPreview = ref<string | null>(null)
const photoError = ref<string | null>(null)
const photoInputRef = ref<HTMLInputElement | null>(null)
const progress = ref(0)
const submitError = ref<string>('')

// Best-effort rarity snapshot: how many trees of this species exist in the
// currently loaded city. Null if the query fails or the tree has no species —
// the check-in must never be blocked on this.
const speciesCityCount = ref<number | null>(null)

async function fetchSpeciesCityCount() {
  const species = props.species?.trim()
  if (!species) return
  try {
    const { query } = useDuckDB()
    const { rows } = await query(
      `SELECT count(*) AS n FROM trees_fast WHERE species = '${species.replace(/'/g, "''")}'`,
    )
    const n = Number(rows[0]?.n)
    if (Number.isFinite(n) && n > 0) speciesCityCount.value = n
  } catch {
    // Achievements simply won't see a rarity value for this check-in.
  }
}

function formatMeters(m: number): string {
  if (m < 1000) return `${Math.round(m)} m`
  return `${(m / 1000).toFixed(2)} km`
}

async function startLocation() {
  state.value = 'locating'
  locationError.value = ''
  try {
    const pos = await getCurrentPosition()
    userLat.value = pos.coords.latitude
    userLng.value = pos.coords.longitude
    distance.value =
      haversineKm(pos.coords.latitude, pos.coords.longitude, props.treeLat, props.treeLng) * 1000
    state.value = distance.value <= MAX_DISTANCE_M ? 'ready' : 'too-far'
  } catch (err) {
    locationError.value = (err as Error).message ?? 'Location unavailable'
    state.value = 'location-error'
  }
}

async function handlePhotoPicked(event: Event) {
  photoError.value = null
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const resized = await resizeImage(file)
    photoBlob.value = resized
    if (photoPreview.value) URL.revokeObjectURL(photoPreview.value)
    photoPreview.value = URL.createObjectURL(resized)
  } catch (err) {
    photoError.value = (err as Error).message ?? 'Could not process image'
  } finally {
    input.value = ''
  }
}

async function handleSubmit() {
  if (userLat.value == null || userLng.value == null) {
    state.value = 'locating'
    return
  }
  state.value = 'uploading'
  progress.value = 0
  submitError.value = ''
  try {
    const city = closestCityTo(props.treeLat, props.treeLng)
    await recordCheckin({
      treeId: props.treeId,
      treeLat: props.treeLat,
      treeLng: props.treeLng,
      userLat: userLat.value,
      userLng: userLng.value,
      distanceMeters: Math.round(distance.value),
      city,
      photoBlob: photoBlob.value ?? undefined,
      species: props.species ?? null,
      treeForm: props.treeForm ?? null,
      dbhInches: props.dbhInches ?? null,
      plantYear: props.plantYear ?? null,
      speciesCityCount: speciesCityCount.value,
      onProgress: (fraction) => {
        progress.value = fraction
      },
    })
    state.value = 'done'
    emit('success')
  } catch (err) {
    submitError.value = (err as Error).message ?? 'Unknown error'
    state.value = 'error'
  }
}

function handleDismiss() {
  if (state.value === 'uploading') return
  emit('close')
}

function handleKey(e: KeyboardEvent) {
  if (e.key === 'Escape') handleDismiss()
}

onMounted(() => {
  window.addEventListener('keydown', handleKey)
  void startLocation()
  void fetchSpeciesCityCount()
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKey)
  if (photoPreview.value) URL.revokeObjectURL(photoPreview.value)
})
</script>

<style scoped>
.checkin-dialog {
  position: fixed;
  inset: 0;
  z-index: 200;
  background: rgba(6, 10, 14, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}

.checkin-dialog__panel {
  width: 100%;
  max-width: 440px;
  max-height: 90vh;
  overflow: auto;
  background:
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 20, 17, 0.98));
  border: 1px solid rgba(167, 227, 178, 0.18);
  box-shadow: 0 24px 56px rgba(6, 8, 10, 0.55);
  display: flex;
  flex-direction: column;
}

.checkin-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.1);
}

.checkin-dialog__title {
  margin: 0;
  font-family: var(--font-display);
  font-size: 1rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-ink);
}

.checkin-dialog__close {
  background: transparent;
  border: 1px solid rgba(167, 227, 178, 0.2);
  color: var(--color-ink);
  width: 30px;
  height: 30px;
  font-size: 0.9rem;
  cursor: pointer;
}

.checkin-dialog__body {
  padding: 16px 18px 18px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.checkin-dialog__tree {
  display: flex;
  gap: 8px;
  align-items: baseline;
  margin: 0;
  font-size: 0.9rem;
}

.checkin-dialog__tree code {
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--color-leaf);
  font-size: 0.82rem;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.muted {
  color: var(--color-muted);
  font-size: 0.85rem;
}

.ok-text {
  color: var(--color-leaf);
}

.error-text {
  color: #ff8a8a;
}

.field-label {
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted);
  margin-bottom: 4px;
}

.photo-field {
  display: flex;
  flex-direction: column;
  cursor: pointer;
}

.photo-input {
  display: none;
}

.photo-drop {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px dashed rgba(167, 227, 178, 0.28);
  background: rgba(28, 31, 36, 0.4);
  font-size: 0.88rem;
}

.photo-drop:hover {
  border-color: var(--color-leaf);
}

.photo-preview-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
}

.photo-preview {
  width: 72px;
  height: 72px;
  object-fit: cover;
  border: 1px solid rgba(167, 227, 178, 0.18);
}

.actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 4px;
}

.btn-primary,
.btn-secondary {
  padding: 10px 16px;
  font-family: var(--font-display);
  font-size: 0.78rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  border: 1px solid rgba(167, 227, 178, 0.3);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.btn-primary {
  background: var(--color-leaf);
  color: #0b0f0d;
  border-color: var(--color-leaf);
}

.btn-primary:hover {
  background: transparent;
  color: var(--color-leaf);
}

.btn-secondary {
  background: transparent;
  color: var(--color-ink);
}

.btn-secondary:hover {
  background: rgba(167, 227, 178, 0.08);
}

.progress {
  width: 100%;
  height: 6px;
  background: rgba(167, 227, 178, 0.12);
  overflow: hidden;
}

.progress__bar {
  height: 100%;
  background: var(--color-leaf);
  transition: width 0.18s ease-out;
}
</style>
