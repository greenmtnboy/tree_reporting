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
        keyTimes="0;0.32;0.51;0.73;0.85;1"
        dur="3.6s"
        repeatCount="indefinite"
        calcMode="spline"
        keySplines=".4 0 .7 .8;.3 .2 .45 1;.4 0 .5 1;.35 0 .5 1;.4 0 .6 1"
      />
    </path>
  </svg>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'

withDefaults(defineProps<{ size?: number }>(), { size: 20 })

// The stem barely moves. The tip dips, rebounds and gives a smaller second nod.
// The two fills share a curved edge so they stay joined as the blade flexes.
const poses = [
  { // Broadside, with the stem tipped up.
    midrib: 'M-12 0 C-5 -2 2 1 11 -3',
    light: 'C7 -12 -2 -11 -12 0Z',
    shade: 'C8 6 -3 10 -12 0Z',
    stem: 'M11 -3 Q12.5 -4 13 -6',
  },
  { // The tip dips as the lit face rolls away.
    midrib: 'M-11 4 C-6 0 3 1 11 -2',
    light: 'C5 -3 -3 -3 -11 4Z',
    shade: 'C7 4 -6 9 -11 4Z',
    stem: 'M11 -2 Q12.5 -3 13.5 -5',
  },
  { // The thin blade cups at the bottom of the dip.
    midrib: 'M-9 6 C-6 1 3 4 10 -2',
    light: 'C4 1 -4 -2 -9 6Z',
    shade: 'C5 6 -4 5 -9 6Z',
    stem: 'M10 -2 Q12 -3 13 -4.5',
  },
  { // The tip springs up before the blade fully opens.
    midrib: 'M-12 -3 C-6 1 4 2 11 -2',
    light: 'C6 -7 -4 -3 -12 -3Z',
    shade: 'C6 5 -5 6 -12 -3Z',
    stem: 'M11 -2 Q13 -3 13.5 -5',
  },
  { // A smaller flex settles back into the broadside pose.
    midrib: 'M-12 1 C-5 -1 3 1 11 -3',
    light: 'C7 -7 -2 -7 -12 1Z',
    shade: 'C8 6 -3 8 -12 1Z',
    stem: 'M11 -3 Q12.5 -4 13 -5.5',
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
}

.light { fill: color-mix(in srgb, var(--color-leaf) 28%, #a8cc77); }
.shade { fill: color-mix(in srgb, var(--color-leaf) 62%, #568849); }
.stem { fill: none; stroke: var(--color-leaf); stroke-width: 1; stroke-linecap: round; }
</style>
