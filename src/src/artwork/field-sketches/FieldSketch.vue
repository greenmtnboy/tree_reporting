<script setup lang="ts">
import { fieldSketches, type FieldSketchName } from './index'

defineProps<{ name: FieldSketchName; grow?: boolean }>()
</script>

<template>
  <!-- Markup comes exclusively from the static local SVG imports above. -->
  <div :key="name" class="field-sketch" :class="{ 'field-sketch--growing': grow }" :data-sketch="name" aria-hidden="true" v-html="fieldSketches[name].svg" />
</template>

<style scoped>
.field-sketch :deep(svg) {
  display: block;
  width: 100%;
  height: auto;
}

/* Reveal the plant upward from its roots; keep the ground still. This is a
   one-time entrance, replayed only when a different biome is mounted. */
.field-sketch--growing :deep(.botanical) {
  transform-box: view-box;
  transform-origin: 76% 100%;
  animation: sketch-grow 2.8s cubic-bezier(.22, .61, .36, 1) .15s both;
}
.field-sketch--growing :deep(.contours),
.field-sketch--growing :deep(.dunes) {
  animation: sketch-ground 1.3s ease-out both;
}
@keyframes sketch-grow {
  from { clip-path: inset(100% 0 0 0); opacity: 0; transform: translateY(10px) scale(.96, .92); }
  20% { opacity: 1; }
  to { clip-path: inset(0); opacity: 1; transform: none; }
}
@keyframes sketch-ground {
  from { opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .field-sketch--growing :deep(.botanical),
  .field-sketch--growing :deep(.contours),
  .field-sketch--growing :deep(.dunes) { animation: none; }
}
</style>
