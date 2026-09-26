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

const COUNTRY_BY_PREFIX: Record<string, string> = {
  US: 'United States',
  AR: 'Argentina',
  FR: 'France',
  CA: 'Canada',
  DE: 'Germany',
  NL: 'Netherlands',
  GB: 'United Kingdom',
  AU: 'Australia',
  GR: 'Greece',
}

const router = useRouter()
const route = useRoute()
const { displayCity, markInitialUserCityDetectionDone } = useMapData()
const { activateCity } = useMapLifecycle()

function formatCityLabel(code: string, name: string) {
  const country = COUNTRY_BY_PREFIX[code.slice(0, 2)]
  return country ? `${name}, ${country}` : name
}

const sortedCities = computed(() =>
  Object.entries(CITY_CONFIG)
    .map(([code, cfg]) => ({ code, label: formatCityLabel(code, cfg.name) }))
    .sort((a, b) => a.label.localeCompare(b.label))
)

function handleChange(e: Event) {
  const city = (e.target as HTMLSelectElement).value as CityCode
  // A manual pick settles the initial-city question — IP/geolocation detection
  // still in flight must not override it.
  markInitialUserCityDetectionDone()
  if (route.name !== 'map') {
    activateCity(city)
  }
  void router.replace({ query: { ...route.query, city } })
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
