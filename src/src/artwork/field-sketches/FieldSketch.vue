<script setup lang="ts">
import { fieldSketches, type FieldSketchName } from './index'

defineProps<{ name: FieldSketchName }>()
</script>

<template>
  <!-- Markup comes exclusively from the static local SVG imports above. -->
  <div class="field-sketch" :data-sketch="name" aria-hidden="true" v-html="fieldSketches[name].svg" />
</template>

<style scoped>
.field-sketch :deep(svg) {
  display: block;
  width: 100%;
  height: auto;
}
</style>
