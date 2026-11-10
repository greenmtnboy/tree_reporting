<template>
  <select
    class="city-select"
    :value="displayCity"
    aria-label="Select city"
    data-testid="city-select"
    @change="handleChange"
  >
    <option v-for="{ code, label } in sortedCities" :key="code" :value="code">
      {{ label }}
    </option>
  </select>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'
import { useMapLifecycle } from '../composables/useMapLifecycle'
import { cityOptionLabels } from './cityOptionLabels'

const router = useRouter()
const route = useRoute()
const { displayCity, markInitialUserCityDetectionDone } = useMapData()
const { activateCity } = useMapLifecycle()

const sortedCities = computed(() => {
  const labels = cityOptionLabels(CITY_CONFIG)
  return Object.keys(CITY_CONFIG)
    .map((code) => ({ code, label: labels[code] }))
    .sort((a, b) => a.label.localeCompare(b.label))
})

function handleChange(e: Event) {
  const select = e.target as HTMLSelectElement
  const city = select.value as CityCode
  // A manual pick settles the initial-city question — IP/geolocation detection
  // still in flight must not override it.
  markInitialUserCityDetectionDone()
  // Hand the keyboard and wheel back to the map: a select that keeps focus
  // turns the next arrow key or scroll into another city switch.
  select.blur()
  const canvas = document.querySelector<HTMLElement>('.tree-map .maplibregl-canvas')
  canvas?.focus({ preventScroll: true })
  if (route.name !== 'map') {
    activateCity(city)
  }
  void router.replace({ query: { ...route.query, city, ...(route.name === 'map' ? { mode: 'explore' } : {}) } })
}
</script>

<style scoped>
.city-select {
  width: auto; min-width: 196px; flex: 1 1 0; padding: 10px 14px;
  border: 1px solid var(--color-border); border-radius: 9px; background: var(--surface-1);
  color: var(--color-ink); font-size: .84rem; font-weight: 500; cursor: pointer;
  transition: border-color .15s;
}
.city-select:hover, .city-select:focus { border-color: var(--color-leaf); }
.city-select option { background: var(--surface-1); color: var(--color-ink); }
</style>
