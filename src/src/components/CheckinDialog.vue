<template>
  <div class="checkin-dialog" role="dialog" aria-modal="true" @click.self="handleDismiss">
    <div class="checkin-dialog__panel">
      <header class="checkin-dialog__header">
        <h2 class="checkin-dialog__title">{{ MODE_TITLES[mode] }}</h2>
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
            You're about {{ formatMeters(distance) }} away. Check-ins and reports require being within
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

          <div class="mode-tabs" role="tablist" aria-label="What would you like to do?">
            <button
              v-for="m in MODES"
              :key="m"
              type="button"
              role="tab"
              class="mode-tab"
              :class="{ 'mode-tab--active': mode === m }"
              :aria-selected="mode === m"
              :data-testid="`checkin-mode-${m}`"
              @click="mode = m"
            >{{ MODE_LABELS[m] }}</button>
          </div>

          <!-- Suggest a fix: position and properties -->
          <template v-if="mode === 'update'">
            <p class="muted">
              Drag the pin to where the trunk actually is, and correct anything that's wrong.
              A reviewer checks every change before it goes on the map.
            </p>
            <div class="picker-wrap">
              <SubmitLocationPicker
                :lat="proposedLat"
                :lng="proposedLng"
                :user-lat="userLat ?? treeLat"
                :user-lng="userLng ?? treeLng"
                :zoom="19"
                :max-zoom="21"
                @update="handlePinMoved"
              />
            </div>
            <p class="muted">
              <template v-if="movedMeters >= MIN_MOVE_M">Moved {{ formatMeters(movedMeters) }} from the mapped spot.</template>
              <template v-else>Position unchanged.</template>
              <button
                v-if="movedMeters >= MIN_MOVE_M"
                type="button"
                class="link-btn"
                @click="resetPin"
              >Reset</button>
            </p>

            <label class="field">
              <span class="field-label">Species (scientific name)</span>
              <input
                v-model="proposedSpecies"
                type="text"
                class="text-input"
                :placeholder="species || 'e.g. Platanus x hispanica'"
              />
            </label>
            <label class="field">
              <span class="field-label">Trunk diameter at chest height (inches)</span>
              <input
                v-model="proposedDbh"
                type="number"
                inputmode="decimal"
                min="0"
                max="400"
                step="0.5"
                class="text-input"
                :placeholder="dbhInches != null ? String(dbhInches) : 'Measured around 4.5 ft up'"
              />
            </label>
          </template>

          <!-- Tree is gone -->
          <template v-else-if="mode === 'missing'">
            <p class="muted">
              Tell us what's here instead. A reviewer checks the report before the tree comes off the map.
            </p>
            <fieldset class="reason-group">
              <legend class="field-label">What did you find?</legend>
              <label v-for="r in MISSING_REASONS" :key="r.value" class="reason-option">
                <input v-model="missingReason" type="radio" name="missing-reason" :value="r.value" />
                <span>{{ r.label }}</span>
              </label>
            </fieldset>
          </template>

          <label v-if="mode !== 'checkin'" class="field">
            <span class="field-label">Notes (optional)</span>
            <textarea
              v-model="notes"
              class="text-input"
              rows="2"
              maxlength="1000"
              :placeholder="mode === 'missing' ? 'e.g. fresh stump, new sidewalk poured' : 'Anything the reviewer should know'"
            ></textarea>
          </label>

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
              <span>{{ PHOTO_PROMPTS[mode] }}</span>
            </span>
            <span v-else class="photo-preview-wrap">
              <img :src="photoPreview" alt="Photo preview" class="photo-preview" />
              <span class="muted">Tap to replace</span>
            </span>
          </label>
          <p v-if="photoError" class="error-text">{{ photoError }}</p>
          <label v-if="mode === 'checkin' && photoBlob" class="share-photo">
            <input v-model="submitPhotoForTree" type="checkbox" data-testid="checkin-share-photo" />
            <span>
              Submit as a photo of this tree
              <span class="muted">— a reviewer checks it before it appears on the tree card.</span>
            </span>
          </label>
          <p v-if="mode === 'update' && dbhInvalid" class="error-text" data-testid="checkin-dbh-invalid">
            Diameter must be a number of inches between 0 and {{ MAX_DBH_IN }}.
          </p>
          <p v-else-if="mode === 'update' && !hasChanges" class="muted">Change the pin, species or diameter to send a fix.</p>

          <div class="actions">
            <button type="button" class="btn-secondary" @click="handleDismiss">Cancel</button>
            <button
              type="button"
              class="btn-primary"
              :disabled="!canSubmit"
              data-testid="checkin-submit"
              @click="handleSubmit"
            >{{ SUBMIT_LABELS[mode] }}</button>
          </div>
        </section>

        <!-- Uploading -->
        <section v-else-if="state === 'uploading'" class="section">
          <p>{{ photoBlob ? 'Uploading photo…' : mode === 'checkin' ? 'Recording check-in…' : 'Sending report…' }}</p>
          <div v-if="photoBlob" class="progress">
            <div class="progress__bar" :style="{ width: `${Math.round(progress * 100)}%` }"></div>
          </div>
        </section>

        <!-- Done -->
        <section v-else-if="state === 'done'" class="section">
          <p class="ok-text"><strong>{{ mode === 'checkin' ? 'Checked in!' : 'Thanks — report sent.' }}</strong></p>
          <p v-if="mode === 'checkin' && photoBlob && submitPhotoForTree" class="muted">
            Your photo is in the review queue. Once approved it shows on this tree's card.
          </p>
          <p v-if="mode !== 'checkin'" class="muted">
            A reviewer will look at it. You can follow it under My contributions.
          </p>
          <div class="actions">
            <button type="button" class="btn-primary" @click="handleDismiss">Close</button>
          </div>
        </section>

        <!-- Error -->
        <section v-else-if="state === 'error'" class="section">
          <p class="error-text"><strong>{{ mode === 'checkin' ? 'Check-in failed.' : 'Report failed.' }}</strong></p>
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
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { getCurrentPosition } from '../lib/geo'
import { resizeImage } from '../lib/image'
import {
  recordCheckin,
  submitTreeModification,
  type MissingReason,
} from '../composables/useSubmissions'
import SubmitLocationPicker from './SubmitLocationPicker.vue'
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
  initialMode?: 'checkin' | 'update' | 'missing'
}>()

const emit = defineEmits<{
  (e: 'close'): void
  // `counted`: whether a check-in added to the tree's public count (at most
  // once per person per tree per 20 hours). Always false for a report.
  (e: 'success', mode: Mode, counted: boolean): void
}>()

// One visit, three things to say about the tree: it's here (check in), it's
// here but mapped wrong (update), or it isn't here (missing). The last two
// are reviewed modifications of the existing tree id, not check-ins.
type Mode = 'checkin' | 'update' | 'missing'
const MODES: Mode[] = ['checkin', 'update', 'missing']
const MODE_LABELS: Record<Mode, string> = {
  checkin: 'Check in',
  update: 'Suggest a fix',
  missing: 'Tree is gone',
}
const MODE_TITLES: Record<Mode, string> = {
  checkin: 'Check in',
  update: 'Suggest a fix',
  missing: 'Report missing tree',
}
const SUBMIT_LABELS: Record<Mode, string> = {
  checkin: 'Check in',
  update: 'Send fix',
  missing: 'Send report',
}
const PHOTO_PROMPTS: Record<Mode, string> = {
  checkin: 'Attach a photo of this tree',
  update: 'Attach a photo that shows the fix',
  missing: 'Attach a photo of the spot',
}
const MISSING_REASONS: { value: MissingReason; label: string }[] = [
  { value: 'removed', label: 'Removed — nothing left' },
  { value: 'stump', label: 'Only a stump' },
  { value: 'never-existed', label: 'Never a tree here' },
  { value: 'other', label: 'Something else' },
]
// The pin only moves when dragged or recentred; below this a nudge is
// treated as unchanged rather than sent as a sub-metre "correction".
const MIN_MOVE_M = 1

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

const mode = ref<Mode>(props.initialMode ?? 'checkin')
const proposedLat = ref(props.treeLat)
const proposedLng = ref(props.treeLng)
const proposedSpecies = ref('')
// v-model on a number input yields a number, or '' when cleared.
const proposedDbh = ref<number | string>('')
const missingReason = ref<MissingReason>('removed')
const notes = ref('')
// Opt-in: a check-in photo stays private unless the user offers it here.
const submitPhotoForTree = ref(false)

const movedMeters = computed(
  () => haversineKm(props.treeLat, props.treeLng, proposedLat.value, proposedLng.value) * 1000,
)

const MAX_DBH_IN = 400

const proposedDbhValue = computed<number | null>(() => {
  if (proposedDbh.value === '' || proposedDbh.value == null) return null
  const n = Number(proposedDbh.value)
  return Number.isFinite(n) && n > 0 && n <= MAX_DBH_IN ? n : null
})

// Something typed that proposedDbhValue refuses, so the user is told why the
// fix is not being sent rather than seeing Send stay disabled.
const dbhInvalid = computed(
  () => proposedDbh.value !== '' && proposedDbh.value != null && proposedDbhValue.value == null,
)

const speciesChanged = computed(() => {
  const next = proposedSpecies.value.trim()
  return next !== '' && next !== (props.species ?? '').trim()
})

const dbhChanged = computed(
  () => proposedDbhValue.value != null && proposedDbhValue.value !== props.dbhInches,
)

const hasChanges = computed(
  () => movedMeters.value >= MIN_MOVE_M || speciesChanged.value || dbhChanged.value,
)

const canSubmit = computed(() => mode.value !== 'update' || hasChanges.value)

function handlePinMoved(payload: { lat: number; lng: number }) {
  proposedLat.value = payload.lat
  proposedLng.value = payload.lng
}

function resetPin() {
  proposedLat.value = props.treeLat
  proposedLng.value = props.treeLng
}

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
  if (!canSubmit.value) return
  state.value = 'uploading'
  progress.value = 0
  submitError.value = ''
  const onProgress = (fraction: number) => {
    progress.value = fraction
  }
  try {
    const city = closestCityTo(props.treeLat, props.treeLng)
    let counted = false
    if (mode.value === 'checkin') {
      ;({ counted } = await recordCheckin({
        treeId: props.treeId,
        treeLat: props.treeLat,
        treeLng: props.treeLng,
        userLat: userLat.value,
        userLng: userLng.value,
        distanceMeters: Math.round(distance.value),
        city,
        photoBlob: photoBlob.value ?? undefined,
        submitPhotoForTree: submitPhotoForTree.value,
        species: props.species ?? null,
        treeForm: props.treeForm ?? null,
        dbhInches: props.dbhInches ?? null,
        plantYear: props.plantYear ?? null,
        speciesCityCount: speciesCityCount.value,
        onProgress,
      }))
    } else {
      const moved = movedMeters.value >= MIN_MOVE_M
      await submitTreeModification({
        kind: mode.value,
        treeId: props.treeId,
        city,
        treeLat: props.treeLat,
        treeLng: props.treeLng,
        userLat: userLat.value,
        userLng: userLng.value,
        distanceMeters: Math.round(distance.value),
        currentSpecies: props.species ?? null,
        currentDbhInches: props.dbhInches ?? null,
        missingReason: missingReason.value,
        proposedLat: moved ? proposedLat.value : null,
        proposedLng: moved ? proposedLng.value : null,
        proposedSpecies: speciesChanged.value ? proposedSpecies.value.trim() : null,
        proposedDbhInches: dbhChanged.value ? proposedDbhValue.value : null,
        notes: notes.value,
        photoBlob: photoBlob.value ?? undefined,
        onProgress,
      })
    }
    state.value = 'done'
    emit('success', mode.value, counted)
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
    linear-gradient(180deg, rgba(var(--surface-rgb), 0.98), rgba(var(--surface-rgb), 0.98));
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  box-shadow: 0 24px 56px rgba(6, 8, 10, 0.55);
  display: flex;
  flex-direction: column;
}

.checkin-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid rgba(var(--accent-rgb), 0.1);
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
  border: 1px solid rgba(var(--accent-rgb), 0.2);
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
  color: var(--color-error);
}

.field-label {
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted);
  margin-bottom: 4px;
}

.mode-tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  border: 1px solid rgba(var(--accent-rgb), 0.22);
}

.mode-tab {
  padding: 9px 6px;
  background: transparent;
  border: none;
  border-right: 1px solid rgba(var(--accent-rgb), 0.22);
  color: var(--color-muted);
  font-family: var(--font-display);
  font-size: 0.68rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  white-space: nowrap;
  cursor: pointer;
}

.mode-tab:last-child {
  border-right: none;
}

.mode-tab--active {
  background: rgba(var(--accent-rgb), 0.14);
  color: var(--color-leaf);
}

.picker-wrap {
  height: 240px;
  border: 1px solid rgba(var(--accent-rgb), 0.18);
}

.picker-wrap :deep(.location-picker) {
  min-height: 0;
}

.field {
  display: flex;
  flex-direction: column;
}

.text-input {
  padding: 9px 10px;
  background: rgba(var(--surface-rgb), 0.6);
  border: 1px solid rgba(var(--accent-rgb), 0.22);
  color: var(--color-ink);
  font: inherit;
  font-size: 0.9rem;
  resize: vertical;
}

.text-input:focus {
  outline: none;
  border-color: var(--color-leaf);
}

.reason-group {
  border: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.reason-option {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 0.9rem;
  cursor: pointer;
}

.share-photo {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 0.88rem;
  cursor: pointer;
}

.share-photo input {
  margin-top: 3px;
  accent-color: var(--color-leaf);
}

.link-btn {
  background: none;
  border: none;
  color: var(--color-leaf);
  text-decoration: underline;
  cursor: pointer;
  font: inherit;
  padding: 0 0 0 6px;
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
  border: 1px dashed rgba(var(--accent-rgb), 0.28);
  background: rgba(var(--surface-rgb), 0.4);
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
  border: 1px solid rgba(var(--accent-rgb), 0.18);
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
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.btn-primary {
  background: var(--color-leaf);
  color: var(--color-on-accent);
  border-color: var(--color-leaf);
}

.btn-primary:hover {
  background: transparent;
  color: var(--color-leaf);
}

.btn-primary:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  background: var(--color-leaf);
  color: var(--color-on-accent);
}

.btn-secondary {
  background: transparent;
  color: var(--color-ink);
}

.btn-secondary:hover {
  background: rgba(var(--accent-rgb), 0.08);
}

.progress {
  width: 100%;
  height: 6px;
  background: rgba(var(--accent-rgb), 0.12);
  overflow: hidden;
}

.progress__bar {
  height: 100%;
  background: var(--color-leaf);
  transition: width 0.18s ease-out;
}
</style>
