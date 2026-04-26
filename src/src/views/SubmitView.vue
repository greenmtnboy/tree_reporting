<template>
  <div class="submit-view">
    <div class="submit-card">
      <header class="submit-header">
        <button type="button" class="submit-back" @click="handleBack" aria-label="Go back">
          ←
        </button>
        <h1>Submit a tree</h1>
        <div class="submit-steps" aria-hidden="true">
          <span :class="['step', { active: step === 'capture' || step === 'uploading' || step === 'confirm' || step === 'locate' || step === 'done' }]">1</span>
          <span :class="['step', { active: step === 'locate' || step === 'confirm' || step === 'uploading' || step === 'done' }]">2</span>
          <span :class="['step', { active: step === 'confirm' || step === 'uploading' || step === 'done' }]">3</span>
        </div>
      </header>

      <!-- Firebase unavailable -->
      <section v-if="!firebaseAvailable" class="submit-body">
        <p>Submissions are unavailable right now. The upload service couldn't be reached.</p>
        <div class="submit-actions">
          <button type="button" class="btn-primary" @click="handleBack">Back to map</button>
        </div>
      </section>

      <!-- Step 1: Capture -->
      <section v-else-if="step === 'capture'" class="submit-body">
        <p class="muted">
          Take a clear photo of the whole tree. You'll pinpoint its location next.
        </p>
        <label class="capture-drop">
          <input
            ref="fileInputRef"
            type="file"
            accept="image/*"
            capture="environment"
            class="capture-input"
            @change="handleFilePicked"
          />
          <span class="capture-drop__icon" aria-hidden="true">📷</span>
          <span class="capture-drop__copy">
            <strong>Take a photo</strong>
            <span class="muted">or pick from your library</span>
          </span>
        </label>
        <p v-if="resizeError" class="error-text">{{ resizeError }}</p>
      </section>

      <!-- Step 2: Locate -->
      <section v-else-if="step === 'locate'" class="submit-body submit-body--locate">
        <div class="locate-copy">
          <p>Drag the pin to the base of the tree.</p>
          <p class="muted" v-if="userAccuracy != null">
            GPS accuracy: +/-{{ Math.round(userAccuracy) }} m
          </p>
        </div>
        <div class="locate-map">
          <SubmitLocationPicker
            :lat="lat"
            :lng="lng"
            :user-lat="userLat"
            :user-lng="userLng"
            :zoom="19"
            :max-zoom="21"
            @update="handleLocationUpdate"
          />
        </div>
        <div class="submit-actions">
          <button type="button" class="btn-secondary" @click="step = 'capture'">Back</button>
          <button type="button" class="btn-primary" @click="step = 'confirm'">Next</button>
        </div>
      </section>

      <!-- Step 3: Confirm -->
      <section v-else-if="step === 'confirm'" class="submit-body">
        <div class="confirm-row">
          <div class="photo-strip">
            <img v-if="previewUrl" :src="previewUrl" alt="Photo preview" class="photo-tile" />
            <div v-for="(p, i) in additionalPhotos" :key="p.previewUrl" class="photo-tile-wrap">
              <img :src="p.previewUrl" :alt="`Additional photo ${i + 1}`" class="photo-tile" />
              <button
                type="button"
                class="photo-remove"
                :aria-label="`Remove additional photo ${i + 1}`"
                @click="removeAdditionalPhoto(i)"
              >×</button>
            </div>
            <label v-if="canAddMorePhotos" class="photo-add">
              <input
                type="file"
                accept="image/*"
                capture="environment"
                class="capture-input"
                @change="handleAdditionalPicked"
              />
              <span class="photo-add__icon" aria-hidden="true">+</span>
              <span class="photo-add__label">Add</span>
            </label>
          </div>
          <div class="confirm-meta">
            <dl class="kv">
              <dt>Location</dt>
              <dd><code>{{ lat.toFixed(6) }}, {{ lng.toFixed(6) }}</code></dd>
              <dt>City</dt>
              <dd>{{ cityLabel }}</dd>
            </dl>
          </div>
        </div>
        <p v-if="additionalPhotoError" class="error-text">{{ additionalPhotoError }}</p>
        <label class="field">
          <span class="field-label">Species (optional)</span>
          <input v-model="species" type="text" placeholder="e.g. Platanus x hispanica" class="text-input" />
          <div v-if="plantnetAvailable" class="identify-row">
            <button
              type="button"
              class="btn-identify"
              :disabled="identifying || !photoBlob"
              @click="handleIdentify"
            >
              {{ identifying ? 'Identifying…' : 'Identify from photo' }}
            </button>
            <span v-if="identifyError" class="error-text">{{ identifyError }}</span>
          </div>
          <div v-if="candidates.length" class="candidate-list">
            <button
              v-for="c in candidates"
              :key="c.scientificName"
              type="button"
              :class="['candidate-chip', { selected: species === c.scientificName }]"
              @click="species = c.scientificName"
            >
              <span class="candidate-sci">{{ c.scientificName }}</span>
              <span v-if="c.commonName" class="candidate-common">{{ c.commonName }}</span>
              <span class="candidate-score">{{ Math.round(c.score * 100) }}%</span>
            </button>
          </div>
          <p v-else-if="identifyAttempted && !identifying && !identifyError" class="muted">
            No matches returned. Try the species field.
          </p>
          <div v-if="identifyAttempted && !identifying" class="plantnet-credit">
            <a href="https://my.plantnet.org/" target="_blank" rel="noopener">
              <img
                :src="plantnetLogo"
                alt="Powered by Pl@ntNet"
                class="plantnet-credit__logo"
              />
            </a>
            <p class="plantnet-credit__text">
              The image-based plant species identification service used, is based on the
              Pl@ntNet recognition API, regularly updated and accessible through the site
              <a href="https://my.plantnet.org/" target="_blank" rel="noopener">https://my.plantnet.org/</a>.
            </p>
          </div>
        </label>
        <label class="field">
          <span class="field-label">Notes (optional)</span>
          <textarea v-model="notes" rows="3" placeholder="Anything helpful for moderators" class="text-input"></textarea>
        </label>
        <p class="muted submit-disclaimer">
          By submitting, you release this photo under
          <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">CC0 (public domain)</a>.
        </p>
        <div class="submit-actions">
          <button type="button" class="btn-secondary" @click="step = 'locate'">Back</button>
          <button type="button" class="btn-primary" @click="handleSubmit">Submit</button>
        </div>
      </section>

      <!-- Step 4: Uploading -->
      <section v-else-if="step === 'uploading'" class="submit-body">
        <p>Uploading…</p>
        <div class="progress">
          <div class="progress__bar" :style="{ width: `${Math.round(progress * 100)}%` }"></div>
        </div>
        <p class="muted">{{ Math.round(progress * 100) }}%</p>
      </section>

      <!-- Step 5: Done -->
      <section v-else-if="step === 'done'" class="submit-body">
        <p><strong>Submitted.</strong> Your contribution is in the queue for review.</p>
        <div class="submit-actions">
          <router-link class="btn-secondary" :to="{ name: 'contributions' }">View contributions</router-link>
          <button type="button" class="btn-primary" @click="reset">Submit another</button>
        </div>
      </section>

      <!-- Step 6: Error -->
      <section v-else-if="step === 'error'" class="submit-body">
        <p class="error-text"><strong>Submission failed.</strong></p>
        <p class="muted">{{ errorMessage }}</p>
        <div class="submit-actions">
          <button type="button" class="btn-secondary" @click="reset">Start over</button>
          <button type="button" class="btn-primary" @click="handleSubmit">Retry upload</button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import SubmitLocationPicker from '../components/SubmitLocationPicker.vue'
import { useMapData, CITY_CONFIG, closestCityTo } from '../composables/useMapData'
import { resizeImage } from '../lib/image'
import {
  acquireSharedPositionWatch,
  refreshSharedPosition,
  useSharedPosition,
  type SharedPosition,
} from '../lib/geo'
import { submitPhoto } from '../composables/useSubmissions'
import { firebaseAvailable } from '../lib/firebase'
import {
  identifySpecies,
  plantnetAvailable,
  PLANTNET_MAX_IMAGES,
  type SpeciesCandidate,
} from '../lib/plantnet'
import plantnetLogo from '../assets/powered-by-plantnet-dark.svg'

type Step = 'capture' | 'locate' | 'confirm' | 'uploading' | 'done' | 'error'

const router = useRouter()
const { selectedCity } = useMapData()

const step = ref<Step>('capture')
const fileInputRef = ref<HTMLInputElement | null>(null)
const photoBlob = ref<Blob | null>(null)
const previewUrl = ref<string | null>(null)
const resizeError = ref<string | null>(null)

const initialLat = ref(0)
const initialLng = ref(0)
const initialAccuracy = ref<number | null>(null)
const userLat = ref(0)
const userLng = ref(0)
const userAccuracy = ref<number | null>(null)
const lat = ref(0)
const lng = ref(0)
const refinedByUser = ref(false)
const detectedCity = ref<string | null>(null)
const hasInitialFix = ref(false)

const species = ref('')
const notes = ref('')

const candidates = ref<SpeciesCandidate[]>([])
const identifying = ref(false)
const identifyError = ref<string | null>(null)
const identifyAttempted = ref(false)

interface AdditionalPhoto {
  blob: Blob
  previewUrl: string
}

const additionalPhotos = ref<AdditionalPhoto[]>([])
const additionalPhotoError = ref<string | null>(null)
const { sharedPosition } = useSharedPosition()
let releaseSharedPositionWatch: (() => void) | null = null

const canAddMorePhotos = computed(
  () => additionalPhotos.value.length < PLANTNET_MAX_IMAGES - 1,
)

async function handleAdditionalPicked(event: Event) {
  additionalPhotoError.value = null
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  if (!canAddMorePhotos.value) {
    additionalPhotoError.value = `Up to ${PLANTNET_MAX_IMAGES} photos total.`
    input.value = ''
    return
  }
  try {
    const resized = await resizeImage(file)
    additionalPhotos.value.push({
      blob: resized,
      previewUrl: URL.createObjectURL(resized),
    })
  } catch (err) {
    additionalPhotoError.value = (err as Error).message ?? 'Could not process image'
  } finally {
    input.value = ''
  }
}

function removeAdditionalPhoto(index: number) {
  const [removed] = additionalPhotos.value.splice(index, 1)
  if (removed) URL.revokeObjectURL(removed.previewUrl)
}

function clearAdditionalPhotos() {
  for (const p of additionalPhotos.value) URL.revokeObjectURL(p.previewUrl)
  additionalPhotos.value = []
  additionalPhotoError.value = null
}

async function handleIdentify() {
  if (!photoBlob.value) return
  identifying.value = true
  identifyError.value = null
  identifyAttempted.value = true
  try {
    const blobs = [photoBlob.value, ...additionalPhotos.value.map((p) => p.blob)]
    candidates.value = await identifySpecies(blobs)
  } catch (err) {
    candidates.value = []
    identifyError.value = (err as Error).message ?? 'Identification failed'
  } finally {
    identifying.value = false
  }
}

const progress = ref(0)
const errorMessage = ref('')

const activeCity = computed(() => detectedCity.value ?? selectedCity.value ?? null)

const cityLabel = computed(() => {
  const code = activeCity.value as keyof typeof CITY_CONFIG | null
  return code && CITY_CONFIG[code] ? CITY_CONFIG[code].name : String(activeCity.value ?? '—')
})

async function handleFilePicked(event: Event) {
  resizeError.value = null
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  try {
    const resized = await resizeImage(file)
    photoBlob.value = resized
    if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
    previewUrl.value = URL.createObjectURL(resized)
    await advanceToLocate()
  } catch (err) {
    resizeError.value = (err as Error).message ?? 'Could not process image'
  } finally {
    input.value = ''
  }
}

function ensureSharedPositionWatch() {
  if (releaseSharedPositionWatch) return
  releaseSharedPositionWatch = acquireSharedPositionWatch()
}

function applyLiveLocation(location: SharedPosition, options: { captureInitial?: boolean } = {}) {
  userLat.value = location.lat
  userLng.value = location.lng
  userAccuracy.value = location.accuracy
  detectedCity.value = closestCityTo(location.lat, location.lng)

  if (options.captureInitial) {
    initialLat.value = location.lat
    initialLng.value = location.lng
    initialAccuracy.value = location.accuracy
    hasInitialFix.value = true
  }

  if (step.value !== 'capture' && !refinedByUser.value) {
    lat.value = location.lat
    lng.value = location.lng
  }
}

async function advanceToLocate() {
  const fallback = CITY_CONFIG[selectedCity.value as keyof typeof CITY_CONFIG]?.center
  try {
    ensureSharedPositionWatch()
    const location = await refreshSharedPosition()
    applyLiveLocation(location, { captureInitial: true })
  } catch {
    initialAccuracy.value = null
    userAccuracy.value = null
    detectedCity.value = selectedCity.value ?? null
    if (fallback) {
      initialLng.value = fallback[0]
      initialLat.value = fallback[1]
      userLng.value = fallback[0]
      userLat.value = fallback[1]
    }
    hasInitialFix.value = true
  }
  lat.value = initialLat.value
  lng.value = initialLng.value
  refinedByUser.value = false
  step.value = 'locate'
}

function handleLocationUpdate(payload: { lat: number; lng: number; source: 'drag' | 'recenter' }) {
  lat.value = payload.lat
  lng.value = payload.lng
  refinedByUser.value = payload.source === 'drag'
    ? payload.lat !== userLat.value || payload.lng !== userLng.value
    : false
}

async function handleSubmit() {
  if (!photoBlob.value) {
    step.value = 'capture'
    return
  }
  const cityCode = activeCity.value
  if (!cityCode) {
    errorMessage.value = 'No city selected — open the map first.'
    step.value = 'error'
    return
  }
  step.value = 'uploading'
  progress.value = 0
  try {
    await submitPhoto({
      photoBlob: photoBlob.value,
      additionalPhotoBlobs: additionalPhotos.value.map((p) => p.blob),
      city: String(cityCode),
      initialLat: initialLat.value,
      initialLng: initialLng.value,
      initialAccuracy: initialAccuracy.value,
      lat: lat.value,
      lng: lng.value,
      refinedByUser: refinedByUser.value,
      species: species.value,
      notes: notes.value,
      onProgress: (fraction) => {
        progress.value = fraction
      },
    })
    step.value = 'done'
  } catch (err) {
    errorMessage.value = (err as Error).message ?? 'Unknown error'
    step.value = 'error'
  }
}

function reset() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  previewUrl.value = null
  photoBlob.value = null
  clearAdditionalPhotos()
  species.value = ''
  notes.value = ''
  progress.value = 0
  errorMessage.value = ''
  refinedByUser.value = false
  hasInitialFix.value = false
  initialLat.value = 0
  initialLng.value = 0
  initialAccuracy.value = null
  userLat.value = 0
  userLng.value = 0
  userAccuracy.value = null
  lat.value = 0
  lng.value = 0
  detectedCity.value = null
  candidates.value = []
  identifyError.value = null
  identifyAttempted.value = false
  step.value = 'capture'
}

function handleBack() {
  if (step.value === 'capture' || step.value === 'done') {
    router.back()
  } else if (step.value === 'locate') {
    step.value = 'capture'
  } else if (step.value === 'confirm') {
    step.value = 'locate'
  } else if (step.value === 'error') {
    step.value = 'confirm'
  }
}

watch(
  sharedPosition,
  (location) => {
    if (!location) return
    applyLiveLocation(location, { captureInitial: !hasInitialFix.value && step.value !== 'capture' })
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value)
  clearAdditionalPhotos()
  releaseSharedPositionWatch?.()
  releaseSharedPositionWatch = null
})
</script>

<style scoped>
.submit-view {
  padding: 24px 16px;
  overflow-y: auto;
  height: 100%;
}

.submit-card {
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.submit-header {
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.1);
  padding-bottom: 14px;
}

.submit-header h1 {
  font-size: 1.3rem;
  font-family: var(--font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin: 0;
  flex: 1;
}

.submit-back {
  background: transparent;
  border: 1px solid rgba(167, 227, 178, 0.2);
  color: var(--color-ink);
  width: 36px;
  height: 36px;
  font-size: 1.1rem;
  cursor: pointer;
}

.submit-back:hover {
  background: rgba(47, 125, 79, 0.15);
}

.submit-steps {
  display: flex;
  gap: 6px;
}

.step {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(167, 227, 178, 0.2);
  font-size: 0.75rem;
  color: var(--color-muted);
}

.step.active {
  color: var(--color-leaf);
  border-color: var(--color-leaf);
}

.submit-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.submit-body--locate {
  gap: 10px;
}

.muted {
  color: var(--color-muted);
  font-size: 0.88rem;
}

.capture-drop {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  border: 1px dashed rgba(167, 227, 178, 0.28);
  background: rgba(28, 31, 36, 0.5);
  cursor: pointer;
}

.capture-drop:hover {
  border-color: var(--color-leaf);
  background: rgba(47, 125, 79, 0.1);
}

.capture-input {
  display: none;
}

.capture-drop__icon {
  font-size: 2rem;
}

.capture-drop__copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.capture-drop__copy strong {
  font-family: var(--font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  font-size: 0.9rem;
}

.locate-copy {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.locate-map {
  height: 55vh;
  min-height: 320px;
  border: 1px solid rgba(167, 227, 178, 0.12);
}

.confirm-row {
  display: flex;
  flex-direction: column;
  gap: 14px;
  align-items: stretch;
}

.photo-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.photo-tile,
.photo-tile-wrap,
.photo-add {
  width: 96px;
  height: 96px;
  flex: 0 0 auto;
}

.photo-tile {
  object-fit: cover;
  border: 1px solid rgba(167, 227, 178, 0.2);
  display: block;
}

.photo-tile-wrap {
  position: relative;
}

.photo-tile-wrap .photo-tile {
  width: 100%;
  height: 100%;
}

.photo-remove {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 22px;
  height: 22px;
  border: none;
  background: rgba(11, 15, 13, 0.75);
  color: var(--color-ink);
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.photo-remove:hover {
  background: rgba(11, 15, 13, 0.95);
  color: #ff8a8a;
}

.photo-add {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  border: 1px dashed rgba(167, 227, 178, 0.28);
  background: rgba(28, 31, 36, 0.5);
  color: var(--color-muted);
  cursor: pointer;
}

.photo-add:hover {
  border-color: var(--color-leaf);
  background: rgba(47, 125, 79, 0.1);
  color: var(--color-leaf);
}

.photo-add__icon {
  font-size: 1.5rem;
  line-height: 1;
}

.photo-add__label {
  font-family: var(--font-display);
  font-size: 0.7rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}

.confirm-meta {
  flex: 1;
}

.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
  margin: 0;
}

.kv dt {
  color: var(--color-muted);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  align-self: center;
}

.kv dd {
  margin: 0;
  font-size: 0.82rem;
}

.kv code {
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--color-leaf);
  font-size: 0.78rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.field-label {
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted);
}

.text-input {
  background: rgba(28, 31, 36, 0.8);
  border: 1px solid rgba(167, 227, 178, 0.14);
  color: var(--color-ink);
  padding: 10px 12px;
  font-size: 0.9rem;
  font-family: inherit;
  outline: none;
}

.text-input:focus {
  border-color: var(--color-leaf);
}

.identify-row {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-top: 6px;
  flex-wrap: wrap;
}

.btn-identify {
  background: transparent;
  color: var(--color-leaf);
  border: 1px solid rgba(167, 227, 178, 0.3);
  padding: 6px 12px;
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  cursor: pointer;
}

.btn-identify:hover:not(:disabled) {
  background: rgba(47, 125, 79, 0.15);
  border-color: var(--color-leaf);
}

.btn-identify:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 8px;
}

.candidate-chip {
  display: grid;
  grid-template-columns: 1fr auto;
  grid-template-rows: auto auto;
  column-gap: 10px;
  text-align: left;
  background: rgba(28, 31, 36, 0.6);
  border: 1px solid rgba(167, 227, 178, 0.14);
  color: var(--color-ink);
  padding: 8px 12px;
  cursor: pointer;
  font-family: inherit;
}

.candidate-chip:hover {
  border-color: var(--color-leaf);
  background: rgba(47, 125, 79, 0.1);
}

.candidate-chip.selected {
  border-color: var(--color-leaf);
  background: rgba(47, 125, 79, 0.18);
}

.candidate-sci {
  font-style: italic;
  font-size: 0.88rem;
}

.candidate-common {
  grid-column: 1;
  color: var(--color-muted);
  font-size: 0.78rem;
}

.candidate-score {
  grid-row: 1 / span 2;
  grid-column: 2;
  align-self: center;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--color-leaf);
  font-size: 0.78rem;
}

.plantnet-credit {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgba(167, 227, 178, 0.1);
  flex-wrap: wrap;
}

.plantnet-credit__logo {
  height: 24px;
  width: auto;
  flex: 0 0 auto;
}

.plantnet-credit__text {
  margin: 0;
  font-size: 0.72rem;
  color: var(--color-muted);
  flex: 1;
  min-width: 200px;
  line-height: 1.4;
}

.plantnet-credit__text a {
  color: var(--color-leaf);
}

.submit-disclaimer {
  font-size: 0.78rem;
}

.submit-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 8px;
}

.btn-primary,
.btn-secondary {
  padding: 10px 18px;
  font-family: var(--font-display);
  font-size: 0.8rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  border: 1px solid rgba(167, 227, 178, 0.3);
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
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

.error-text {
  color: #ff8a8a;
  font-size: 0.88rem;
}
</style>

