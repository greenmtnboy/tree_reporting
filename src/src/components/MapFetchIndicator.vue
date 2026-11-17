<template>
  <Transition name="map-fetching">
    <div v-if="visible" class="map-fetching" :class="{ 'map-fetching--mobile': mobile }" role="status" aria-live="polite">
      <LeafSpinner :size="20" />
      <span class="map-fetching-text">
        <span class="map-fetching-phrase">{{ phrase }}</span>
        <em class="map-fetching-detail">{{ FETCH_DETAIL }}</em>
      </span>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import LeafSpinner from './LeafSpinner.vue'
import { FETCH_DETAIL, FETCH_PHRASES } from '../lib/loadProgress'

/**
 * A corner leaf while the worker fetches blocks under the camera. It waits a
 * beat before showing, so a cached pan does not flash it, and once shown it
 * stays long enough to read.
 */
const props = defineProps<{ active: boolean; mobile?: boolean }>()

const SHOW_AFTER_MS = 250
const MIN_VISIBLE_MS = 700
const PHRASE_MS = 2600

const visible = ref(false)
const phrase = ref(FETCH_PHRASES[0])
let showTimer: ReturnType<typeof setTimeout> | null = null
let hideTimer: ReturnType<typeof setTimeout> | null = null
let phraseTimer: ReturnType<typeof setInterval> | null = null
let shownAt = 0

function show() {
  showTimer = null
  visible.value = true
  shownAt = Date.now()
  let idx = 0
  phrase.value = FETCH_PHRASES[idx]
  phraseTimer = setInterval(() => {
    idx = (idx + 1) % FETCH_PHRASES.length
    phrase.value = FETCH_PHRASES[idx]
  }, PHRASE_MS)
}

function hide() {
  hideTimer = null
  visible.value = false
  if (phraseTimer) clearInterval(phraseTimer)
  phraseTimer = null
}

function clearTimers() {
  if (showTimer) clearTimeout(showTimer)
  if (hideTimer) clearTimeout(hideTimer)
  showTimer = hideTimer = null
}

watch(() => props.active, (active) => {
  clearTimers()
  if (active) {
    if (!visible.value) showTimer = setTimeout(show, SHOW_AFTER_MS)
  } else if (visible.value) {
    hideTimer = setTimeout(hide, Math.max(0, MIN_VISIBLE_MS - (Date.now() - shownAt)))
  }
}, { immediate: true })

onUnmounted(() => {
  clearTimers()
  hide()
})
</script>

<style scoped>
.map-fetching {
  position: absolute;
  left: 12px;
  bottom: 36px;
  z-index: 4;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 14px 7px 10px;
  border-radius: 14px;
  background: rgba(var(--surface-rgb), 0.9);
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  box-shadow: 0 8px 20px rgba(7, 10, 11, 0.22);
  color: var(--color-ink);
  pointer-events: none;
  max-width: calc(100% - 24px);
  white-space: nowrap;
}

/* Above the bottom navigation, level with the legend on the other side. */
.map-fetching--mobile {
  bottom: 104px;
}

.map-fetching-text {
  display: grid;
  gap: 1px;
}

.map-fetching-phrase {
  font-size: 0.78rem;
  font-weight: 600;
}

.map-fetching-detail {
  font-size: 0.68rem;
  color: var(--color-muted);
}

.map-fetching-enter-active,
.map-fetching-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.map-fetching-enter-from,
.map-fetching-leave-to {
  opacity: 0;
  transform: translateY(6px);
}
</style>
