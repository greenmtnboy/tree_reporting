<template>
  <svg class="leaf-spinner" :style="{ width: `${size}px`, height: `${size}px` }" viewBox="0 0 24 24" aria-hidden="true">
    <g class="leaf-spinner-leaf">
      <path d="M12 2.5C7 6 5 10 6.2 14.6c.9 3.3 3.3 5.4 5.8 6.9 2.5-1.5 4.9-3.6 5.8-6.9C19 10 17 6 12 2.5Z" fill="currentColor" />
      <path d="M12 5.5v15.5M12 10.5l-2.6-2M12 13.5l3-2.4M12 16.5l-2.8-2.1" fill="none" stroke="var(--leaf-spinner-vein, rgba(255, 255, 255, 0.55))" stroke-width="1" stroke-linecap="round" />
    </g>
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ size?: number }>(), { size: 20 })
</script>

<style scoped>
.leaf-spinner {
  flex-shrink: 0;
  color: var(--color-leaf);
  overflow: visible;
}

/* A leaf caught in an eddy: a full turn with a flutter, not a mechanical spin. */
.leaf-spinner-leaf {
  transform-origin: 12px 12px;
  transform-box: view-box;
  animation: leaf-twirl 1.8s cubic-bezier(0.45, 0.05, 0.4, 0.95) infinite;
}

@keyframes leaf-twirl {
  0% { transform: rotate(0deg) scale(1, 1); }
  25% { transform: rotate(100deg) scale(0.75, 1); }
  50% { transform: rotate(180deg) scale(1, 1); }
  75% { transform: rotate(280deg) scale(0.75, 1); }
  100% { transform: rotate(360deg) scale(1, 1); }
}

@media (prefers-reduced-motion: reduce) {
  .leaf-spinner-leaf { animation: leaf-sway 2.4s ease-in-out infinite; }
  @keyframes leaf-sway {
    0%, 100% { transform: rotate(-12deg); }
    50% { transform: rotate(12deg); }
  }
}
</style>
