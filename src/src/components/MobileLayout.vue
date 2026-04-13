<template>
  <div class="mobile-layout" :data-mobile-screen="currentScreen">
    <div v-if="isMapScreen" class="mobile-map-container mobile-screen">
      <TreeMap simplified />
    </div>

    <div v-else class="mobile-route-screen mobile-screen">
      <header class="mobile-route-header">
        <div class="mobile-route-heading">
          <span class="mobile-route-eyebrow">Active city</span>
          <strong class="mobile-route-title">{{ routeTitle }}</strong>
        </div>
        <CitySelector />
      </header>
      <div class="mobile-route-body">
        <SummaryView v-if="isSummaryScreen" />
        <SpeciesView v-else-if="isSpeciesScreen" />
        <InfoView v-else />
      </div>
    </div>

    <div class="mobile-bottom-bar" :class="{ 'mobile-bottom-bar--overlay-open': !!activeOverlay }">
      <div class="mobile-bottom-bar-actions">
        <button
          v-for="action in visibleActions"
          :key="action.key"
          class="mobile-action-btn"
          :class="{ 'mobile-action-btn--active': activeOverlay === action.key }"
          :aria-label="action.ariaLabel"
          :aria-pressed="activeOverlay === action.key"
          @click="action.onClick"
        >
          <svg v-if="action.key === 'landmarks'" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          {{ action.label }}
        </button>
      </div>

      <div class="mobile-nav-wrap">
        <transition name="mobile-fade">
          <div v-if="navMenuOpen" class="mobile-nav-menu" aria-label="Mobile navigation">
            <button
              v-for="item in navItems"
              :key="item.screen"
              class="mobile-nav-item"
              :class="{ 'mobile-nav-item--active': currentScreen === item.screen }"
              @click="goToScreen(item.screen)"
            >
              <span class="mobile-nav-item-label">{{ item.label }}</span>
              <span class="mobile-nav-item-copy">{{ item.copy }}</span>
            </button>
          </div>
        </transition>
        <button
          class="mobile-nav-trigger"
          :class="{ 'mobile-nav-trigger--open': navMenuOpen }"
          aria-label="Open navigation menu"
          @click="navMenuOpen = !navMenuOpen"
        >
          <span></span>
          <span></span>
          <span></span>
        </button>
      </div>
    </div>

    <transition name="mobile-slide">
      <div v-if="activeOverlay === 'landmarks'" class="mobile-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">Search Landmarks</span>
        </div>
        <div class="mobile-overlay-body">
          <input
            v-model="search"
            type="text"
            class="mobile-search-input"
            placeholder="Search landmarks..."
          />
          <div class="mobile-landmarks-list">
            <button
              v-for="lm in filtered"
              :key="lm.name"
              class="mobile-landmark-item"
              @click="handleLandmarkClick(lm)"
            >
              {{ lm.name }}
            </button>
            <div v-if="!landmarkLoading && filtered.length === 0" class="mobile-landmarks-empty">
              No landmarks found
            </div>
          </div>
        </div>
      </div>
    </transition>

    <transition name="mobile-slide">
      <div v-if="activeOverlay === 'chat'" class="mobile-overlay mobile-chat-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">{{ chatOverlayTitle }}</span>
        </div>
        <ChatPanel />
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TreeMap from './TreeMap.vue'
import ChatPanel from './ChatPanel.vue'
import CitySelector from './CitySelector.vue'
import SummaryView from '../views/SummaryView.vue'
import SpeciesView from '../views/SpeciesView.vue'
import InfoView from '../views/InfoView.vue'
import { useLandmarkData } from '../composables/useLandmarkData'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData } from '../composables/useMapData'
import type { Landmark } from '../types'

type MobileOverlay = 'landmarks' | 'chat' | null
type MobileScreen = 'map' | 'summary' | 'species' | 'info'
type MobileAction = {
  key: 'landmarks' | 'chat'
  label: string
  ariaLabel: string
  onClick: () => void
}

const route = useRoute()
const router = useRouter()
const { selectedCity } = useMapData()
const { landmarks, loading: landmarkLoading } = useLandmarkData()
const { flyTo } = useFlyTo()

const activeOverlay = ref<MobileOverlay>(null)
const navMenuOpen = ref(false)
const search = ref('')

const currentScreen = computed<MobileScreen>(() => {
  if (route.name === 'summary') return 'summary'
  if (route.name === 'species') return 'species'
  if (route.name === 'info') return 'info'
  return 'map'
})

const isMapScreen = computed(() => currentScreen.value === 'map')
const isSummaryScreen = computed(() => currentScreen.value === 'summary')
const isSpeciesScreen = computed(() => currentScreen.value === 'species')

const routeTitle = computed(() => {
  if (isSummaryScreen.value) return 'City Summary'
  if (isSpeciesScreen.value) return 'Species Explorer'
  return 'Project Info'
})

const chatOverlayTitle = computed(() => {
  if (isSummaryScreen.value) return 'Analytics Assistant'
  if (isSpeciesScreen.value) return 'Species Assistant'
  if (isMapScreen.value) return 'Tree Assistant'
  return 'Project Assistant'
})

const navItems: Array<{ screen: MobileScreen; label: string; copy: string }> = [
  { screen: 'map', label: 'Map', copy: 'Explore trees and landmarks' },
  { screen: 'summary', label: 'Analytics', copy: 'Inspect city summary charts' },
  { screen: 'species', label: 'Species', copy: 'Browse taxa, traits, and filters' },
  { screen: 'info', label: 'Info', copy: 'Read sources and project notes' },
]

const visibleActions = computed<MobileAction[]>(() => {
  const actions: MobileAction[] = []
  if (isMapScreen.value) {
    actions.push({
      key: 'landmarks',
      label: 'Landmarks',
      ariaLabel: 'Open landmarks search',
      onClick: () => openOverlay('landmarks'),
    })
  }
  actions.push({
    key: 'chat',
    label: 'Chat',
    ariaLabel: isSummaryScreen.value
      ? 'Open analytics chat'
      : isSpeciesScreen.value
        ? 'Open species chat'
        : isMapScreen.value
          ? 'Open tree assistant'
          : 'Open project chat',
    onClick: () => openOverlay('chat'),
  })
  return actions
})

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return landmarks.value
  return landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
})

watch(
  () => route.name,
  () => {
    activeOverlay.value = null
    navMenuOpen.value = false
  },
)

function openOverlay(overlay: Exclude<MobileOverlay, null>) {
  navMenuOpen.value = false
  activeOverlay.value = activeOverlay.value === overlay ? null : overlay
}

function goToScreen(screen: MobileScreen) {
  navMenuOpen.value = false
  if (currentScreen.value === screen) {
    return
  }
  void router.push({
    name: screen,
    query: { ...route.query, city: selectedCity.value },
  })
}

function handleLandmarkClick(lm: Landmark) {
  flyTo({ lng: lm.lng, lat: lm.lat, zoom: 16, label: lm.name })
  activeOverlay.value = null
}
</script>

<style scoped>
.mobile-layout {
  display: flex;
  flex-direction: column;
  height: 100%;
  width: 100%;
  position: relative;
}

.mobile-screen {
  min-height: 0;
}

.mobile-map-container {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.mobile-route-screen {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding-bottom: 82px;
  box-sizing: border-box;
  background:
    radial-gradient(circle at top left, rgba(47, 125, 79, 0.16), transparent 44%),
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 20, 17, 0.98));
}

.mobile-route-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 18px 14px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.22), rgba(28, 31, 36, 0.88));
}

.mobile-route-heading {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.mobile-route-eyebrow {
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.mobile-route-title {
  font-family: var(--font-display);
  font-size: 1rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-ink);
}

.mobile-route-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.mobile-route-body :deep(.summary-page),
.mobile-route-body :deep(.species-page),
.mobile-route-body :deep(.info-page) {
  height: 100%;
}

.mobile-bottom-bar {
  position: absolute;
  left: 14px;
  right: 14px;
  bottom: 16px;
  z-index: 30;
  display: flex;
  gap: 10px;
  pointer-events: none;
}

.mobile-bottom-bar--overlay-open {
  z-index: 120;
}

.mobile-bottom-bar-actions {
  display: flex;
  flex: 1 1 auto;
  gap: 10px;
  min-width: 0;
}

.mobile-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 42px;
  min-width: 0;
  padding: 0 14px;
  border-radius: 16px;
  border: 1px solid rgba(167, 227, 178, 0.14);
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.64), rgba(28, 31, 36, 0.96));
  color: rgba(237, 242, 235, 0.82);
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  box-shadow: 0 14px 28px rgba(7, 10, 11, 0.28);
  pointer-events: auto;
}

.mobile-action-btn {
  flex: 1 1 0;
}

.mobile-action-btn:active {
  background: rgba(47, 125, 79, 0.24);
  color: var(--color-leaf);
}

.mobile-action-btn--active {
  border-color: rgba(167, 227, 178, 0.26);
  background:
    linear-gradient(180deg, rgba(67, 107, 77, 0.82), rgba(28, 55, 38, 0.98));
  color: var(--color-ink);
}

.mobile-nav-wrap {
  position: relative;
  flex: 0 0 76px;
  width: 76px;
  pointer-events: auto;
}

.mobile-nav-menu {
  position: fixed;
  left: 14px;
  right: 14px;
  bottom: 72px;
  width: auto;
  max-width: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border: 1px solid rgba(167, 227, 178, 0.1);
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.94), rgba(18, 20, 24, 0.98));
  box-shadow: 0 18px 38px rgba(6, 8, 10, 0.38);
  pointer-events: auto;
}

.mobile-nav-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border: 1px solid rgba(167, 227, 178, 0.06);
  background: rgba(28, 31, 36, 0.56);
  color: rgba(237, 242, 235, 0.84);
  text-align: left;
}

.mobile-nav-item--active {
  border-color: rgba(167, 227, 178, 0.22);
  background: rgba(47, 125, 79, 0.18);
}

.mobile-nav-item-label {
  font-size: 0.8rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.mobile-nav-item-copy {
  font-size: 0.74rem;
  line-height: 1.4;
  color: rgba(154, 166, 154, 0.82);
}

.mobile-nav-trigger {
  width: 100%;
  min-height: 50px;
  border-radius: 16px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background:
    radial-gradient(circle at top, rgba(167, 227, 178, 0.18), transparent 62%),
    linear-gradient(180deg, rgba(42, 47, 54, 0.98), rgba(19, 22, 27, 0.98));
  box-shadow: 0 18px 36px rgba(6, 8, 10, 0.38);
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 10px;
  gap: 5px;
  color: var(--color-ink);
  pointer-events: auto;
  transition: border-color 0.18s ease, background 0.18s ease, box-shadow 0.18s ease;
}

.mobile-nav-trigger--open {
  border-color: rgba(167, 227, 178, 0.26);
  background:
    linear-gradient(180deg, rgba(67, 107, 77, 0.82), rgba(28, 55, 38, 0.98));
  box-shadow: 0 18px 36px rgba(12, 22, 16, 0.42);
}

.mobile-nav-trigger span {
  width: 22px;
  height: 2px;
  border-radius: 999px;
  background: currentColor;
  transition: transform 0.18s ease, opacity 0.18s ease;
}

.mobile-nav-trigger--open span:nth-child(1) {
  transform: translateY(7px) rotate(45deg);
}

.mobile-nav-trigger--open span:nth-child(2) {
  opacity: 0;
}

.mobile-nav-trigger--open span:nth-child(3) {
  transform: translateY(-7px) rotate(-45deg);
}

/* Overlays */
.mobile-overlay {
  position: fixed;
  top: max(72px, calc(env(safe-area-inset-top, 0px) + 16px));
  left: 14px;
  right: 14px;
  bottom: 74px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(167, 227, 178, 0.1);
  border-radius: 24px;
  box-shadow: 0 24px 48px rgba(6, 8, 10, 0.44);
  background:
    linear-gradient(180deg, rgba(28, 31, 36, 0.95), rgba(15, 36, 23, 0.97));
}

.mobile-overlay-header {
  display: flex;
  align-items: center;
  padding: 14px 16px;
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.42), rgba(28, 31, 36, 0.96));
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  min-height: 52px;
}

.mobile-overlay-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--color-ink);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

/* Search overlay body */
.mobile-overlay-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.mobile-search-input {
  margin: 12px;
  padding: 10px 14px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  background: rgba(42, 47, 54, 0.72);
  color: var(--color-ink);
  font-size: 1rem;
  outline: none;
  transition: border-color 0.15s;
}

.mobile-search-input:focus {
  border-color: rgba(167, 227, 178, 0.3);
}

.mobile-search-input::placeholder {
  color: rgba(154, 166, 154, 0.7);
}

.mobile-landmarks-list {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

.mobile-landmark-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 14px 16px;
  border: none;
  background: none;
  color: rgba(237, 242, 235, 0.78);
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
}

.mobile-landmark-item:active {
  background: rgba(47, 125, 79, 0.18);
  color: var(--color-leaf);
}

.mobile-landmarks-empty {
  padding: 24px 16px;
  font-size: 0.9rem;
  color: rgba(154, 166, 154, 0.74);
  font-style: italic;
  text-align: center;
}

/* Info overlay body */
.mobile-info-body {
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

/* Slide transition */
.mobile-fade-enter-active,
.mobile-fade-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.mobile-fade-enter-from,
.mobile-fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.mobile-slide-enter-active,
.mobile-slide-leave-active {
  transition: opacity 0.24s ease, transform 0.3s ease;
}

.mobile-slide-enter-from,
.mobile-slide-leave-to {
  opacity: 0;
  transform: translateY(100%);
}
</style>

<!-- Non-scoped overrides for embedded ChatPanel -->
<style>
.mobile-chat-overlay .chat-panel {
  width: 100% !important;
  min-width: 0 !important;
  border-left: none !important;
  flex: 1;
  height: auto !important;
  min-height: 0;
  overflow: hidden;
  border-radius: 0 0 24px 24px;
}

.mobile-chat-overlay .chat-header {
  display: none;
}

.mobile-chat-overlay .chat-panel {
  background:
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 20, 17, 0.98));
}
</style>
