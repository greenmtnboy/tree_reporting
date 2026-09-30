<template>
  <section class="missions" aria-labelledby="missions-title">
    <h2 id="missions-title">Your city missions</h2>
    <label>Explore badges in
      <select v-model="city" aria-label="Mission city">
        <option v-for="(config, code) in CITY_CONFIG" :key="code" :value="code">{{ config.name }}</option>
      </select>
    </label>
    <p class="muted">{{ getCityBiome(city) }} · Rankings describe mapped trees, not every tree in the city. Rare: ≤1% of identified trees; unusual: >1–5%; common: >5%.</p>
    <p v-if="loading" role="status">Finding missions…</p>
    <p v-else-if="failed" role="status">City rankings aren't available yet. Your check-ins and existing badges still count. <button type="button" @click="load">Retry</button></p>
    <p v-else-if="!missions.length">No missions with enough mapped targets yet. Try another city.</p>
    <ul v-else class="mission-list">
      <li v-for="mission in missions" :key="mission.id" class="mission" :data-mission="mission.key">
        <strong>{{ mission.emoji }} {{ mission.title }}</strong>
        <p>{{ mission.description }}</p>
        <span>{{ mission.points }} points · {{ mission.progress }} / {{ mission.target }}</span>
        <progress :value="mission.progress" :max="mission.target" :aria-label="mission.title" />
        <span v-if="mission.earned">Badge earned</span>
        <router-link v-else-if="mission.nextTree" :to="{ name: 'map', query: { city, tree: mission.nextTree.treeId } }">Find a target on the map →</router-link>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { CITY_CONFIG, useMapData } from '../composables/useMapData'
import { getCityBiome } from '../composables/dashboardContextSource'
import { buildMissions, type MissionCheckin, type RankedTree } from '../lib/missions'
import { loadMissionTrees } from '../lib/treeRankings'

const props = defineProps<{ checkins: MissionCheckin[] }>()
const { displayCity } = useMapData()
const route = useRoute()
const routeCity = typeof route.query.city === 'string' && route.query.city in CITY_CONFIG ? route.query.city : null
const city = ref(routeCity ?? displayCity.value)
const trees = ref<RankedTree[]>([])
const loading = ref(false)
const failed = ref(false)
let request = 0
async function load() {
  const token = ++request
  trees.value = []
  loading.value = true
  failed.value = false
  try {
    const result = await loadMissionTrees(city.value)
    if (token === request) trees.value = result
  } catch {
    if (token === request) failed.value = true
  } finally {
    if (token === request) loading.value = false
  }
}
watch(() => route.query.city, next => {
  if (typeof next === 'string' && next in CITY_CONFIG) city.value = next
})
watch(city, load, { immediate: true })
const missions = computed(() => buildMissions(city.value, trees.value, props.checkins))
</script>

<style scoped>
.missions { display: grid; gap: 12px; }
h2, p { margin: 0; }
select { max-width: 100%; padding: 8px; color: var(--color-ink); background: var(--color-surface); }
.muted { color: var(--color-muted); font-size: .8rem; line-height: 1.5; }
.mission-list { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; padding: 0; margin: 0; list-style: none; }
.mission { display: flex; flex-direction: column; gap: 10px; padding: 16px; border: 1px solid rgba(var(--accent-rgb), .3); }
.mission p { flex: 1; }
progress { width: 100%; accent-color: var(--color-leaf); }
a { color: var(--color-leaf); }
</style>
