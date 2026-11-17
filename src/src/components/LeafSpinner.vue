<template>
  <svg class="leaf-spinner" :height="size" viewBox="0 0 40 24" aria-hidden="true">
    <g transform="translate(20 12)">
      <g class="leaf-drift">
        <g class="leaf-blade">
          <path d="M0 7.3C-4.6 6.7-7.1 2.7-5.2-2.6C-4-5.9-.4-7.7 3.1-10.2C2.8-6.6 6.4-2.6 5.2 1.9C4.5 4.8 2.5 6.7 0 7.3Z" fill="currentColor" />
          <path d="M0 7.3C-1.6 2.5.3-4.2 3.1-10.2C2.8-6.6 6.4-2.6 5.2 1.9C4.5 4.8 2.5 6.7 0 7.3Z" class="leaf-fold" />
          <path d="M-2.2 10.2Q-.3 9 0 7.1" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
          <path d="M0 7.1C-1.3 3.4-.1-2.7 2.1-7.4M-.5 3.5Q1.6 2.6 3.2.7M-.2.7Q-2.3-.5-3.3-2.4" class="leaf-veins" />
        </g>
      </g>
    </g>
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 })
</script>

<style scoped>
.leaf-spinner {
  flex: none;
  width: auto;
  overflow: visible;
  color: var(--color-leaf);
  --flight-time: 3.2s;
}

.leaf-fold { fill: color-mix(in srgb, currentColor 70%, #e8de99); }

.leaf-veins {
  fill: none;
  stroke: var(--surface-1);
  stroke-width: 0.75;
  stroke-linecap: round;
  opacity: 0.65;
}

.leaf-blade { transform: rotate(38deg); }

@media (prefers-reduced-motion: no-preference) {
  /* A gust lifts the leaf to the right. It slips down through the same current
     and swings wide to the left before the next lift. The end tangents match. */
  .leaf-drift {
    offset-path: path('M -5 1 C 0 -1 6 -6 9 -4 C 14 0 6 6 0 4 C -5 2 -9 -3 -11 -1 C -14 2 -10 3 -5 1 Z');
    offset-anchor: 0px 0px;
    offset-rotate: 0deg;
    animation: drift var(--flight-time) cubic-bezier(.32, .18, .68, .82) infinite;
  }

  /* Bank into the lift, turn edge-on at the crest, then open out on the glide. */
  .leaf-blade {
    animation: bank var(--flight-time) ease-in-out infinite;
  }

  @keyframes drift {
    to { offset-distance: 100%; }
  }

  @keyframes bank {
    0%, 100% { transform: rotate(38deg) scaleX(.92); }
    24% { transform: rotate(68deg) scaleX(1); }
    38% { transform: rotate(88deg) scaleX(.22); }
    52% { transform: rotate(55deg) scaleX(-.86); }
    76% { transform: rotate(-28deg) scaleX(-.68); }
    88% { transform: rotate(4deg) scaleX(.38); }
  }
}
</style>
