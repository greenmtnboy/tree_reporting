<script setup lang="ts">
import { computed } from 'vue'
import FieldSketch from '../artwork/field-sketches/FieldSketch.vue'
import { getCityBiome } from '../composables/dashboardContextSource'
import { useMapData } from '../composables/useMapData'

const { displayCity } = useMapData()
const ecosystem = computed(() => {
  const biome = getCityBiome(displayCity.value).toLowerCase()
  if (biome.includes('conifer')) return 'conifer'
  if (biome.includes('mediterranean')) return 'woodland'
  if (biome.includes('desert')) return 'desert'
  if (biome.includes('grassland')) return 'grassland'
  return 'broadleaf'
})
</script>

<template>
  <div class="field-backdrop" aria-hidden="true" :data-ecosystem="ecosystem">
    <FieldSketch :key="ecosystem" :name="ecosystem" grow class="ecosystem-study" />
  </div>
</template>

<style scoped>
.field-backdrop {
  position: fixed !important;
  inset: 0;
  z-index: 0 !important;
  pointer-events: none;
  overflow: hidden;
}
.ecosystem-study {
  position: absolute;
  bottom: -32px;
  right: -35px;
  width: min(510px, 50vw);
  color: var(--sketch-leaf);
  opacity: .4;
}
@media (max-width: 768px) {
  .ecosystem-study { width: 75vw; opacity: .25; }
}
</style>
