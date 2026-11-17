<template>
  <div
    ref="rootEl"
    class="map-compass"
    :class="{ 'map-compass--collapsed': !isOpen, 'map-compass--mobile': props.collapsible }"
    role="group"
    aria-label="Map compass"
  >
    <div v-if="isOpen" class="compass-dial" :style="{ transform: `rotate(${-props.bearing}deg)` }">
      <!-- The face is drawn about its own origin, so every tick and the rose
           turn on the dial's exact centre whatever size the dial is. -->
      <svg class="compass-face" viewBox="-50 -50 100 100" aria-hidden="true">
        <line
          v-for="tick in TICKS"
          :key="tick.angle"
          class="compass-tick"
          :class="{ 'compass-tick--major': tick.major }"
          x1="0"
          y1="-46.5"
          x2="0"
          :y2="tick.major ? -41 : -43.5"
          :transform="`rotate(${tick.angle})`"
        />
        <polygon class="compass-rose-cross" points="0,-3.2 10,0 0,3.2 -10,0" />
        <polygon class="compass-rose-north" points="0,-17 -4.6,0 0,0" />
        <polygon class="compass-rose-north compass-rose--shade" points="0,-17 4.6,0 0,0" />
        <polygon class="compass-rose-south" points="0,17 4.6,0 0,0" />
        <polygon class="compass-rose-south compass-rose--shade" points="0,17 -4.6,0 0,0" />
        <circle class="compass-rose-pin" r="1.7" />
      </svg>
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

    <!-- Collapsed puck: a bare needle showing where north is. Tapping it opens
         the dial; once open it is an unpainted hub over the rose that closes it. -->
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
      <svg
        v-if="!isOpen"
        class="compass-puck"
        viewBox="-24 -24 48 48"
        :style="{ transform: `rotate(${-props.bearing}deg)` }"
        aria-hidden="true"
      >
        <polygon class="compass-rose-north" points="0,-17 -5.4,0 0,0" />
        <polygon class="compass-rose-north compass-rose--shade" points="0,-17 5.4,0 0,0" />
        <polygon class="compass-rose-south" points="0,17 5.4,0 0,0" />
        <polygon class="compass-rose-south compass-rose--shade" points="0,17 -5.4,0 0,0" />
        <circle class="compass-rose-pin" r="2" />
      </svg>
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

/** Rim ticks every 22.5 degrees, longer at the intercardinals; the cardinals
 *  are left clear for their letters. */
const TICKS = Array.from({ length: 16 }, (_, i) => ({ angle: i * 22.5, major: i % 2 === 0 }))
  .filter((tick) => tick.angle % 90 !== 0)

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
 * Place a glyph at its cardinal position on the dial (the radius is the
 * layout's, set in CSS), then counter-rotate it by the dial's own rotation so
 * the letter stays upright on screen.
 */
function dirStyle(bearing: number) {
  return {
    transform: `rotate(${bearing}deg) translateY(calc(var(--compass-radius) * -1)) rotate(${props.bearing - bearing}deg)`,
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
  /* The dial's size, how far out the letters sit, and how big a target each is.
     Everything else is drawn in proportion to the dial. */
  --compass-size: 72px;
  --compass-radius: 22px;
  --compass-hit: 20px;
  /* The one warm note on the dial: where north is. */
  --compass-north: var(--color-autumn);

  position: absolute;
  /* Sits immediately left of the zoom control column in the top-right. */
  top: 10px;
  right: 49px;
  z-index: 4;
  box-sizing: border-box;
  width: var(--compass-size);
  height: var(--compass-size);
  border-radius: 50%;
  background: rgba(var(--surface-rgb), 0.88);
  border: 1px solid rgba(var(--accent-rgb), 0.22);
  box-shadow:
    0 10px 24px rgba(7, 10, 11, 0.24),
    inset 0 0 0 2px rgba(var(--surface-rgb), 0.9),
    inset 0 0 0 3px rgba(var(--accent-rgb), 0.12);
  transition: width 0.16s ease, height 0.16s ease;
}

/* Mobile stacks a city-selector bar across the top, so drop down past it and
   sit alongside the zoom-level readout instead. The bar is 40px tall at a
   10px offset, so 58px hangs the puck 8px under it: the same gap it keeps
   from the zoom column. The open dial is larger than the desktop's: its
   letters are touch targets. */
.map-compass--mobile {
  --compass-size: 92px;
  --compass-radius: 29px;
  --compass-hit: 28px;
  top: 58px;
}

/* Anchored top-right, so collapsing shrinks back toward the corner it opens
   from. The puck is exactly as wide as the zoom column beside it. */
.map-compass--collapsed {
  --compass-size: 31px;
  box-shadow: 0 10px 24px rgba(7, 10, 11, 0.24);
}

.compass-dial {
  position: absolute;
  inset: 0;
  /* Bearing changes stream in per frame during rotation, so no CSS transition
     here: the map animation supplies the easing. */
  will-change: transform;
}

.compass-face {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.compass-tick {
  stroke: rgba(var(--ink-rgb), 0.28);
  stroke-width: 1;
  stroke-linecap: round;
}

.compass-tick--major {
  stroke: rgba(var(--ink-rgb), 0.5);
  stroke-width: 1.3;
}

/* The rose: a north-south needle over a faint east-west cross, each half split
   down its spine into a lit and a shaded facet. */
.compass-rose-cross {
  fill: rgba(var(--ink-rgb), 0.16);
}

.compass-rose-north {
  fill: var(--compass-north);
}

.compass-rose-south {
  fill: rgba(var(--ink-rgb), 0.42);
}

.compass-rose--shade {
  fill-opacity: 0.62;
}

.compass-rose-pin {
  fill: rgb(var(--surface-rgb));
}

.compass-dir {
  position: absolute;
  top: 50%;
  left: 50%;
  width: var(--compass-hit);
  height: var(--compass-hit);
  margin: calc(var(--compass-hit) / -2) 0 0 calc(var(--compass-hit) / -2);
  display: grid;
  place-items: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  color: rgba(var(--ink-rgb), 0.72);
  font-family: var(--font-display);
  font-size: 0.72rem;
  font-weight: 700;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.map-compass--mobile .compass-dir {
  font-size: 0.86rem;
}

.compass-dir--north {
  color: var(--compass-north);
}

@media (hover: hover) {
  .compass-dir:hover {
    background: rgba(var(--accent-rgb), 0.24);
    color: var(--color-ink);
  }
}

.compass-dir--facing {
  background: rgba(var(--accent-rgb), 0.2);
  color: var(--color-ink);
}

.compass-dir--north.compass-dir--facing {
  color: var(--compass-north);
}

.compass-dir:disabled {
  /* Dimmed rather than hidden: the dial keeps spinning while the camera is
     driven by an animation, it just isn't clickable yet. */
  opacity: 0.55;
  cursor: default;
  pointer-events: none;
}

.compass-toggle {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  margin: auto;
  display: grid;
  place-items: center;
  padding: 0;
  border: none;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}

/* Once open, the toggle shrinks to a hub over the rose so it doesn't swallow
   taps meant for the cardinal buttons. */
.compass-toggle--open {
  width: 28px;
  height: 28px;
}

.compass-toggle:disabled {
  opacity: 0.55;
  cursor: default;
  pointer-events: none;
}

/* Collapsed needle: warm half points north, dim half points south. */
.compass-puck {
  display: block;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
</style>
