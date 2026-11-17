<template>
  <svg class="leaf-spinner" :height="size * 1.5" viewBox="-18 -18 36 36" aria-hidden="true">
    <path
      v-for="(frames, part) in contours"
      :key="part"
      :class="part"
      :d="frames[0]"
    >
      <animate
        v-if="motionAllowed"
        attributeName="d"
        :values="frames.join(';')"
        keyTimes="0;0.22;0.39;0.59;0.78;1"
        dur="3.6s"
        repeatCount="indefinite"
        calcMode="spline"
        keySplines=".4 0 .78 .7;.26 .36 .65 .75;.36 .25 .6 1;.4 0 .6 .76;.35 .24 .58 1"
      />
    </path>
  </svg>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

withDefaults(defineProps<{ size?: number }>(), { size: 20 })

// Both halves follow the same curved midrib, from the tip to the stem.
// Only the contour rolls over. The leaf stays within a few pixels of its center.
const poses = [
  { // Broadside, with the stem tipped up.
    midrib: 'M-12 0 C-5 -2 2 1 11 -3',
    light: 'C5 -2 1 -12 -12 0Z',
    shade: 'C5 3 -2 9 -12 0Z',
    stem: 'M11 -3 Q12.5 -4 13 -6',
  },
  { // Almost edge-on. Keep a sliver of the underside visible.
    midrib: 'M-12 0 C-5 0 3 3 12 1',
    light: 'C5 2 -3 -2 -12 0Z',
    shade: 'C5 5 -3 4 -12 0Z',
    stem: 'M12 1 Q13.5 1 14 0',
  },
  { // The underside opens first and the tip lags behind.
    midrib: 'M-10 -3 C-6 2 1 4 10 4',
    light: 'C4 3 -1 -3 -10 -3Z',
    shade: 'C4 4 -6 8 -10 -3Z',
    stem: 'M10 4 Q11.5 4 13 5.5',
  },
  { // The tip hooks under while the stem is still aloft.
    midrib: 'M-6 8 C-10 1 -4 -6 8 -4',
    light: 'C1 -9 -13 -4 -6 8Z',
    shade: 'C2 -3 -5 0 -6 8Z',
    stem: 'M8 -4 Q10 -4 11.5 -2',
  },
  { // The blade unfurls into a shallow cup.
    midrib: 'M-11 2 C-6 -2 2 5 10 -2',
    light: 'C4 1 -3 -7 -11 2Z',
    shade: 'C5 8 -3 6 -11 2Z',
    stem: 'M10 -2 Q12 -3.5 12.5 -5',
  },
]

const loop = [...poses, poses[0]]
const contours = {
  light: loop.map(pose => pose.midrib + pose.light),
  shade: loop.map(pose => pose.midrib + pose.shade),
  stem: loop.map(pose => pose.stem),
}

const motionAllowed = ref(false)
let preference: MediaQueryList | undefined
function syncMotion() {
  motionAllowed.value = preference?.matches ?? false
}

onMounted(() => {
  preference = window.matchMedia?.('(prefers-reduced-motion: no-preference)')
  syncMotion()
  preference?.addEventListener('change', syncMotion)
})
onUnmounted(() => preference?.removeEventListener('change', syncMotion))
</script>

<style scoped>
.leaf-spinner {
  flex: none;
  width: auto;
  stroke: color-mix(in srgb, var(--color-autumn) 70%, #372a13);
  stroke-width: .75;
  stroke-linejoin: round;
}

.light { fill: color-mix(in srgb, var(--color-autumn) 25%, #f6b655); }
.shade { fill: var(--color-autumn); }
.stem { fill: none; stroke-width: 1; stroke-linecap: round; }
</style>
