<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <h1>Urban Trees</h1>
      <div class="subtitle">The Woods of the Concrete Jungle</div>
    </div>
    <nav class="sidebar-nav">
      <router-link :to="mapRoute">
        <span class="nav-icon">&#x1f5fa;</span>
        Map
      </router-link>
      <router-link :to="summaryRoute">
        <span class="nav-icon">&#x1f4ca;</span>
        Analytics
      </router-link>
      <router-link :to="infoRoute">
        <span class="nav-icon">&#x2139;</span>
        Info
      </router-link>
    </nav>
    <div class="sidebar-landmarks">
      <div class="landmarks-header">Landmarks</div>
      <input
        v-model="search"
        type="text"
        class="landmarks-search"
        placeholder="Search landmarks..."
      />
      <div class="landmarks-list">
        <button
          v-for="lm in filtered"
          :key="lm.name"
          class="landmark-item"
          @click="handleClick(lm)"
        >
          {{ lm.name }}
        </button>
        <div v-if="!landmarkLoading && filtered.length === 0" class="landmarks-empty">
          No landmarks found
        </div>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useLandmarkData } from '../composables/useLandmarkData'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData } from '../composables/useMapData'
import type { Landmark } from '../types'

const { landmarks, loading: landmarkLoading } = useLandmarkData()
const { flyTo } = useFlyTo()
const { selectedCity } = useMapData()

const search = ref('')

const routeQuery = computed(() => ({ city: selectedCity.value }))
const mapRoute = computed(() => ({ path: '/', query: routeQuery.value }))
const summaryRoute = computed(() => ({ path: '/summary', query: routeQuery.value }))
const infoRoute = computed(() => ({ path: '/info', query: routeQuery.value }))

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return landmarks.value
  return landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
})

function handleClick(lm: Landmark) {
  flyTo({ lng: lm.lng, lat: lm.lat, zoom: 16, label: lm.name })
}
</script>

<style scoped>
.sidebar-landmarks {
  display: flex;
  flex-direction: column;
  border-top: 1px solid #0f3460;
  flex: 1;
  min-height: 0;
}

.landmarks-header {
  padding: 10px 16px 6px;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: #7a7a9e;
}

.landmarks-search {
  margin: 0 12px 8px;
  padding: 6px 10px;
  border: 1px solid #0f3460;
  border-radius: 4px;
  background: #1a1a2e;
  color: #e0e0e0;
  font-size: 0.8rem;
  outline: none;
  transition: border-color 0.15s;
}

.landmarks-search:focus {
  border-color: #4fc3f7;
}

.landmarks-search::placeholder {
  color: #555577;
}

.landmarks-list {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 8px;
}

.landmark-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 7px 16px;
  border: none;
  background: none;
  color: #a0a0c0;
  font-size: 0.8rem;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  line-height: 1.3;
}

.landmark-item:hover {
  background: rgba(15, 52, 96, 0.5);
  color: #4fc3f7;
}

.landmarks-empty {
  padding: 12px 16px;
  font-size: 0.75rem;
  color: #555577;
  font-style: italic;
}

.landmarks-list::-webkit-scrollbar {
  width: 4px;
}

.landmarks-list::-webkit-scrollbar-track {
  background: transparent;
}

.landmarks-list::-webkit-scrollbar-thumb {
  background: #0f3460;
  border-radius: 2px;
}
</style>
