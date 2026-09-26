<template>
  <div v-if="route.name !== 'species'" class="top-bar">
    <div class="top-bar-context">
      <span class="top-bar-label">Active city</span>
      <CitySelector />
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import CitySelector from './CitySelector.vue'

const route = useRoute()
</script>

<style scoped>
.top-bar { height: 64px; border-bottom: 1px solid var(--color-border); display: flex; align-items: center; padding: 0 22px; gap: 12px; flex-shrink: 0; z-index: 5; position: relative; background: rgba(var(--surface-rgb), .5); }
.top-bar-context { display: flex; align-items: center; gap: 14px; }
.top-bar-label { font-size: .65rem; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--color-muted); }
</style>
