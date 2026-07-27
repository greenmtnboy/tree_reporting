<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <h1>Urban Trees</h1>
      <div class="subtitle">The Concrete Jungle</div>
      <a
        href="https://github.com/greenmtnboy/sf_tree_reporting"
        target="_blank"
        rel="noopener"
        class="source-link"
      >source code</a>
    </div>
    <nav class="sidebar-nav">
      <router-link :to="mapRoute" class="nav-link">
        <span class="nav-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
            <path d="M3 6.5 9 4l6 2.5 6-2.5v13L15 19l-6-2.5L3 19z" />
            <path d="M9 4v12.5" />
            <path d="M15 6.5V19" />
          </svg>
        </span>
        <span class="nav-copy">
          <strong>Map</strong>
        </span>
      </router-link>
      <router-link :to="summaryRoute" class="nav-link">
        <span class="nav-icon" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <rect x="3" y="12" width="4" height="9" />
            <rect x="10" y="7" width="4" height="14" />
            <rect x="17" y="3" width="4" height="18" />
          </svg>
        </span>
        <span class="nav-copy">
          <strong>City Summary</strong>
        </span>
      </router-link>
      <router-link :to="speciesRoute" class="nav-link">
        <span class="nav-icon" aria-hidden="true">
          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M7 10L12 2l5 8" />
            <path d="M12 6l-8 13h16l-8-13z" />
            <path d="M12 19v3" />
          </svg>
        </span>
        <span class="nav-copy">
          <strong>Species</strong>
        </span>
      </router-link>
      <router-link :to="infoRoute" class="nav-link">
        <span class="nav-icon nav-icon--text" aria-hidden="true">&#9432;</span>
        <span class="nav-copy">
          <strong>Info</strong>
        </span>
      </router-link>
    </nav>
    <div v-if="showLandmarksSection" class="sidebar-landmarks">
      <div class="landmarks-header">
        <span>Landmarks</span>
      </div>
      <div class="landmarks-search-wrap">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <circle cx="11" cy="11" r="7"></circle>
          <path d="m20 20-3.5-3.5"></path>
        </svg>
        <input
          v-model="search"
          type="text"
          class="landmarks-search"
          placeholder="Search landmarks..."
        />
      </div>
      <div class="landmarks-list">
        <button
          v-for="lm in filtered"
          :key="lm.name"
          class="landmark-item"
          @click="handleClick(lm)"
        >
          <span class="landmark-name">{{ lm.name }}</span>
        </button>
        <div v-if="!landmarkLoading && filtered.length === 0" class="landmarks-empty">
          No landmarks found
        </div>
      </div>
    </div>
    <router-link v-if="firebaseAvailable" to="/profile" class="sidebar-profile" :class="{ 'is-active': route.name === 'profile' }">
      <span class="sidebar-profile__icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21v-1a7 7 0 0 1 7-7h2a7 7 0 0 1 7 7v1" />
        </svg>
      </span>
      <span class="sidebar-profile__copy">
        <strong>{{ profileLabel }}</strong>
        <span v-if="profileSubLabel" class="sidebar-profile__sub">{{ profileSubLabel }}</span>
      </span>
    </router-link>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute } from 'vue-router'
import { useLandmarkData } from '../composables/useLandmarkData'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData } from '../composables/useMapData'
import { useAuth } from '../composables/useAuth'
import { firebaseAvailable } from '../lib/firebase'
import type { Landmark } from '../types'

const { landmarks, loading: landmarkLoading } = useLandmarkData()
const { flyTo } = useFlyTo()
const { displayCity } = useMapData()
const { user, authReady, isAnonymous, isGoogleLinked } = useAuth()
const route = useRoute()

const search = ref('')
const showLandmarksSection = computed(() => route.name !== 'summary' && route.name !== 'species')

const profileLabel = computed(() => {
  if (!authReady.value) return 'Sign in'
  return user.value ? 'Profile' : 'Sign in'
})
const profileSubLabel = computed(() => {
  if (!authReady.value || !user.value) return null
  if (isAnonymous.value) return 'anonymous'
  if (user.value.email) return user.value.email
  if (isGoogleLinked.value) return 'google linked'
  return null
})

// displayCity, not selectedCity: mid-swoop the committed city still lags behind
// the city we're flying to, and these links must carry the destination city.
const routeQuery = computed(() => ({ city: displayCity.value }))
const mapRoute = computed(() => ({ path: '/', query: routeQuery.value }))
const summaryRoute = computed(() => ({ path: '/summary', query: routeQuery.value }))
const speciesRoute = computed(() => ({ path: '/species', query: routeQuery.value }))
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
.nav-link {
  position: relative;
}

.nav-copy {
  display: flex;
  flex-direction: column;
  justify-content: center;
  min-width: 0;
  min-height: 28px;
}

.nav-copy strong {
  font-family: var(--font-display);
  font-size: 0.86rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.sidebar-landmarks {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 2px 14px 14px 20px;
  position: relative;
  z-index: 1;
}

.landmarks-header {
  padding: 6px 0 8px;
}

.landmarks-header span {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-muted);
  letter-spacing: 0.14em;
  text-transform: uppercase;
}

.landmarks-search-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 10px;
  padding: 0 10px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.9);
  color: var(--color-moss);
  min-height: 40px;
}

.landmarks-search {
  width: 100%;
  border: none;
  background: transparent;
  color: var(--color-ink);
  font-size: 0.8rem;
  outline: none;
}

.landmarks-search::placeholder {
  color: rgba(154, 166, 154, 0.7);
}

.landmarks-list {
  flex: 1;
  overflow-y: auto;
  padding: 2px 0 6px;
}

.landmark-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border: none;
  background: rgba(42, 47, 54, 0.34);
  color: rgba(237, 242, 235, 0.82);
  font-size: 0.8rem;
  cursor: pointer;
  transition:
    background 0.12s,
    color 0.12s,
    transform 0.12s;
  line-height: 1.3;
  margin-bottom: 4px;
}

.landmark-item:hover {
  background: rgba(47, 125, 79, 0.14);
  color: var(--color-ink);
  transform: translateX(2px);
}

.nav-icon--text {
  font-size: 1rem;
}

.landmarks-empty {
  padding: 12px 4px;
  font-size: 0.75rem;
  color: rgba(154, 166, 154, 0.72);
  font-style: italic;
}

.landmarks-list::-webkit-scrollbar {
  width: 4px;
}

.landmarks-list::-webkit-scrollbar-track {
  background: transparent;
}

.landmarks-list::-webkit-scrollbar-thumb {
  background: rgba(107, 175, 146, 0.3);
  border-radius: 2px;
}

.source-link {
  display: inline-block;
  margin-top: 4px;
  font-size: 0.72rem;
  color: var(--color-moss);
  text-decoration: none;
  letter-spacing: 0.04em;
  opacity: 0.8;
  transition: opacity 0.15s, color 0.15s;
}

.source-link:hover {
  opacity: 1;
  color: var(--color-leaf);
}

.sidebar-profile {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: auto;
  padding: 14px 20px;
  border-top: 1px solid rgba(167, 227, 178, 0.1);
  color: rgba(237, 242, 235, 0.78);
  text-decoration: none;
  transition: background 0.15s, color 0.15s, transform 0.15s;
  position: relative;
  z-index: 1;
}

.sidebar-profile:hover {
  color: var(--color-ink);
  background: rgba(47, 125, 79, 0.08);
}

.sidebar-profile.is-active {
  color: var(--color-leaf);
}

.sidebar-profile__icon {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid rgba(167, 227, 178, 0.14);
  background: rgba(58, 64, 72, 0.28);
  color: var(--color-moss);
  flex-shrink: 0;
}

.sidebar-profile.is-active .sidebar-profile__icon {
  color: var(--color-leaf);
  border-color: rgba(167, 227, 178, 0.35);
}

.sidebar-profile__copy {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.2;
}

.sidebar-profile__copy strong {
  font-family: var(--font-display);
  font-size: 0.86rem;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.sidebar-profile__sub {
  font-size: 0.68rem;
  color: var(--color-muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  margin-top: 2px;
}
</style>
