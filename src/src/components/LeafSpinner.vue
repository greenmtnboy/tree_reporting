<template>
  <svg class="leaf-spinner" :height="size * 1.5" viewBox="0 0 36 36" aria-hidden="true">
    <g transform="translate(18 18)">
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
  --flight-timing: 3.2s linear infinite;
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
  /* A narrow S on the way down, then an updraft. The end tangents meet so
     the same leaf stays in flight across the loop boundary. */
  .leaf-drift {
    offset-path: path('M 1 -9 C 5 -8 4 -4 1 -1 S -4 5 -2 8 C 1 12 4 9 3 5 C 2 1 -4 -4 -3 -8 C -2.5 -10 -1 -9.5 1 -9 Z');
    offset-anchor: 0px 0px;
    offset-rotate: 0deg;
    animation: drift var(--flight-timing);
  }

  /* Spin in a tilted plane. The changing tilt lets the tip sweep an ellipse. */
  .leaf-blade {
    animation: twirl var(--flight-timing);
  }

  @keyframes drift {
    from { offset-distance: 0%; }
    65% {
      offset-distance: 52%;
      animation-timing-function: cubic-bezier(.32, .19, .68, .81);
    }
    to { offset-distance: 100%; }
  }

  @keyframes twirl {
    0% { transform: rotate(-16deg) scaleY(.78) rotate(12deg); }
    32% { transform: rotate(12deg) scaleY(.48) rotate(122deg); }
    68% { transform: rotate(-20deg) scaleY(.84) rotate(263deg); }
    100% { transform: rotate(-16deg) scaleY(.78) rotate(372deg); }
  }
}
</style>
