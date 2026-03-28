<template>
  <select class="city-select" :value="selectedCity" aria-label="Select city" @change="handleChange">
    <option v-for="(cfg, code) in CITY_CONFIG" :key="code" :value="code">
      {{ cfg.name }}
    </option>
  </select>
</template>

<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useMapData, CITY_CONFIG, type CityCode } from '../composables/useMapData'

const router = useRouter()
const route = useRoute()
const { selectedCity } = useMapData()

function handleChange(e: Event) {
  const city = (e.target as HTMLSelectElement).value as CityCode
  void router.replace({ query: { ...route.query, city } })
}
</script>

<style scoped>
.city-select {
  width: auto;
  min-width: 196px;
  flex: 1 1 0;
  padding: 10px 36px 10px 14px;
  border: 1px solid rgba(167, 227, 178, 0.14);
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.58), rgba(28, 31, 36, 0.94));
  color: var(--color-ink);
  font-size: 0.84rem;
  font-weight: 600;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  cursor: pointer;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%23A7E3B2' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 14px center;
  transition:
    border-color 0.15s,
    transform 0.15s,
    box-shadow 0.15s;
}

.city-select:focus {
  border-color: rgba(167, 227, 178, 0.32);
  box-shadow: inset 0 0 0 1px rgba(167, 227, 178, 0.12);
}

.city-select:hover {
  border-color: rgba(107, 175, 146, 0.28);
  transform: translateY(-1px);
}

.city-select option {
  background: var(--color-asphalt);
  color: var(--color-ink);
}
</style>
