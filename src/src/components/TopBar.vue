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
.top-bar {
  height: 58px;
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.04) 0%, rgba(28, 31, 36, 0.3) 42%, rgba(28, 31, 36, 0.56) 100%);
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  display: flex;
  align-items: center;
  padding: 0 18px;
  gap: 12px;
  flex-shrink: 0;
  z-index: 5;
  position: relative;
}

.top-bar::after {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: 0;
  height: 1px;
  background: linear-gradient(90deg, rgba(77, 163, 255, 0), rgba(77, 163, 255, 0.2), rgba(167, 227, 178, 0.26), rgba(77, 163, 255, 0));
}

.top-bar::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.015) 0%, transparent 38%);
  pointer-events: none;
}

.top-bar-context {
  display: flex;
  align-items: center;
  gap: 12px;
}

.top-bar-label {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--color-moss);
}
</style>
