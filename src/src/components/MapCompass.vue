<template>
  <div
    ref="rootEl"
    class="map-compass"
    :class="{ 'map-compass--collapsed': !isOpen, 'map-compass--mobile': props.collapsible }"
    role="group"
    aria-label="Map compass"
  >
    <div v-if="isOpen" class="compass-dial" :style="{ transform: `rotate(${-props.bearing}deg)` }">
      <span class="compass-needle" aria-hidden="true"></span>
      <button
        v-for="dir in DIRECTIONS"
        :key="dir.label"
        type="button"
        class="compass-dir"
        :class="{
          'compass-dir--north': dir.bearing === 0,
          'compass-dir--facing': isFacing(dir.bearing),
        }"
        :style="dirStyle(dir.bearing)"
        :title="`Look ${dir.name}`"
        :aria-label="`Look ${dir.name}`"
        :disabled="props.disabled"
        @click="choose(dir.bearing)"
      >{{ dir.label }}</button>
    </div>

    <!-- Collapsed puck: a bare needle showing where north is. Tapping it opens the dial. -->
    <button
      v-if="props.collapsible"
      type="button"
      class="compass-toggle"
      :class="{ 'compass-toggle--open': isOpen }"
      :title="isOpen ? 'Hide compass' : 'Show compass'"
      :aria-label="isOpen ? 'Hide compass' : 'Show compass'"
      :aria-expanded="isOpen"
      :disabled="props.disabled"
      @click="isOpen = !isOpen"
    >
      <span
        v-if="!isOpen"
        class="compass-toggle-needle"
        :style="{ transform: `rotate(${-props.bearing}deg)` }"
        aria-hidden="true"
      ></span>
    </button>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

const props = withDefaults(
  defineProps<{ bearing: number; disabled?: boolean; collapsible?: boolean }>(),
  { disabled: false, collapsible: false },
)

const emit = defineEmits<{ select: [bearing: number] }>()

const DIRECTIONS = [
  { label: 'N', name: 'north', bearing: 0 },
  { label: 'E', name: 'east', bearing: 90 },
  { label: 'S', name: 'south', bearing: 180 },
  { label: 'W', name: 'west', bearing: 270 },
] as const

/** Distance from the dial center to a cardinal glyph. */
const DIAL_RADIUS_PX = 21
/** How close the camera has to be to a cardinal before it reads as "facing" it. */
const FACING_TOLERANCE_DEG = 4

const rootEl = ref<HTMLElement | null>(null)
// Non-collapsible (desktop) is permanently open; collapsible (mobile) starts shut.
const isOpen = ref(!props.collapsible)

function isFacing(bearing: number): boolean {
  // Normalise the difference into [-180, 180) before comparing.
  const delta = ((((props.bearing - bearing) % 360) + 540) % 360) - 180
  return Math.abs(delta) <= FACING_TOLERANCE_DEG
}

/**
 * Place a glyph at its cardinal position on the dial, then counter-rotate it by
 * the dial's own rotation so the letter stays upright on screen.
 */
function dirStyle(bearing: number) {
  return {
    transform: `rotate(${bearing}deg) translateY(-${DIAL_RADIUS_PX}px) rotate(${props.bearing - bearing}deg)`,
  }
}

function choose(bearing: number) {
  emit('select', bearing)
  // Hand the map back to the user once a direction is picked.
  if (props.collapsible) isOpen.value = false
}

function onDocumentPointerDown(e: PointerEvent) {
  if (!isOpen.value || !props.collapsible) return
  if (rootEl.value?.contains(e.target as Node)) return
  isOpen.value = false
}

onMounted(() => document.addEventListener('pointerdown', onDocumentPointerDown))
onUnmounted(() => document.removeEventListener('pointerdown', onDocumentPointerDown))
</script>

<style scoped>
.map-compass {
  position: absolute;
  /* Sits immediately left of the zoom control column in the top-right. */
  top: 10px;
  right: 49px;
  z-index: 4;
  width: 72px;
  height: 72px;
  border-radius: 50%;
  background: rgba(28, 31, 36, 0.82);
  border: 1px solid rgba(167, 227, 178, 0.16);
  box-shadow: 0 10px 24px rgba(7, 10, 11, 0.24);
  transition: width 0.16s ease, height 0.16s ease;
}

/* Anchored top-right, so collapsing shrinks back toward the corner it opens from. */
.map-compass--collapsed {
  width: 31px;
  height: 31px;
}

/* Mobile stacks a city-selector bar across the top, so drop down past it and
   sit alongside the zoom-level readout instead. The bar is ~45px tall
   (8px offset + the select's height), so 54px clears it. */
.map-compass--mobile {
  top: 54px;
}

.compass-dial {
  position: absolute;
  inset: 0;
  /* Bearing changes stream in per frame during rotation, so no CSS transition
     here — the map animation supplies the easing. */
  will-change: transform;
}

/* North marker, pinned to the dial rim above the "N" glyph. */
.compass-needle {
  position: absolute;
  top: 3px;
  left: 50%;
  width: 0;
  height: 0;
  margin-left: -4px;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-bottom: 7px solid rgba(167, 227, 178, 0.6);
  pointer-events: none;
}

.compass-dir {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 20px;
  height: 20px;
  margin: -10px 0 0 -10px;
  display: grid;
  place-items: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: rgba(237, 242, 235, 0.6);
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.compass-dir:hover {
  background: rgba(47, 125, 79, 0.28);
  color: var(--color-ink);
}

.compass-dir--north {
  color: var(--color-leaf);
}

.compass-dir--facing {
  background: rgba(47, 125, 79, 0.22);
  color: var(--color-leaf);
}

.compass-dir:disabled {
  /* Dimmed rather than hidden — the dial keeps spinning while the camera is
     driven by an animation, it just isn't clickable yet. */
  opacity: 0.55;
  cursor: default;
  pointer-events: none;
}

.compass-toggle {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 100%;
  height: 100%;
  transform: translate(-50%, -50%);
  display: grid;
  place-items: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}

/* Once open, the toggle shrinks to a hub in the dial center so it doesn't
   swallow taps meant for the cardinal buttons. */
.compass-toggle--open {
  width: 20px;
  height: 20px;
  background: rgba(167, 227, 178, 0.14);
}

.compass-toggle:disabled {
  /* Dimmed rather than hidden — the dial keeps spinning while the camera is
     driven by an animation, it just isn't clickable yet. */
  opacity: 0.55;
  cursor: default;
  pointer-events: none;
}

/* Collapsed needle: solid leaf half points north, dim half points south. */
.compass-toggle-needle {
  position: relative;
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 8px solid var(--color-leaf);
  pointer-events: none;
}

.compass-toggle-needle::after {
  content: '';
  position: absolute;
  top: 8px;
  left: -5px;
  width: 0;
  height: 0;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 8px solid rgba(237, 242, 235, 0.3);
}
</style>
