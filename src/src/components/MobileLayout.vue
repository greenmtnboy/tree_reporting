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
        <InfoView v-else />
      </div>
    </div>

    <div v-if="isMapScreen && !activeOverlay" class="mobile-map-actions">
      <button class="mobile-action-btn" @click="openOverlay('landmarks')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        Landmarks
      </button>
      <button class="mobile-action-btn" @click="openOverlay('chat')">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        Chat
      </button>
    </div>

    <button
      v-if="isSummaryScreen && !activeOverlay"
      class="mobile-chat-fab"
      aria-label="Open analytics chat"
      @click="openOverlay('chat')"
    >
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
      </svg>
      Chat
    </button>

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
        class="mobile-nav-fab"
        :class="{ 'mobile-nav-fab--open': navMenuOpen }"
        aria-label="Open navigation menu"
        @click="navMenuOpen = !navMenuOpen"
      >
        <span></span>
        <span></span>
        <span></span>
      </button>
    </div>

    <transition name="mobile-slide">
      <div v-if="activeOverlay === 'landmarks'" class="mobile-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">Search Landmarks</span>
          <button class="mobile-overlay-close" @click="activeOverlay = null">&times;</button>
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
          <span class="mobile-overlay-title">{{ isSummaryScreen ? 'Analytics Assistant' : 'Tree Assistant' }}</span>
          <button class="mobile-overlay-close" @click="activeOverlay = null">&times;</button>
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
import InfoView from '../views/InfoView.vue'
import { useLandmarkData } from '../composables/useLandmarkData'
import { useFlyTo } from '../composables/useFlyTo'
import { useMapData } from '../composables/useMapData'
import type { Landmark } from '../types'

type MobileOverlay = 'landmarks' | 'chat' | null
type MobileScreen = 'map' | 'summary' | 'info'

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
  if (route.name === 'info') return 'info'
  return 'map'
})

const isMapScreen = computed(() => currentScreen.value === 'map')
const isSummaryScreen = computed(() => currentScreen.value === 'summary')

const routeTitle = computed(() =>
  isSummaryScreen.value ? 'City Summary' : 'Project Info',
)

const navItems: Array<{ screen: MobileScreen; label: string; copy: string }> = [
  { screen: 'map', label: 'Map', copy: 'Explore trees and landmarks' },
  { screen: 'summary', label: 'Analytics', copy: 'Inspect city summary charts' },
  { screen: 'info', label: 'Info', copy: 'Read sources and project notes' },
]

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
  activeOverlay.value = overlay
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
.mobile-route-body :deep(.info-page) {
  height: 100%;
}

.mobile-map-actions {
  position: absolute;
  left: 14px;
  right: 92px;
  bottom: 16px;
  z-index: 30;
  display: flex;
  gap: 10px;
  pointer-events: none;
}

.mobile-action-btn,
.mobile-chat-fab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 46px;
  padding: 0 16px;
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
  flex: 1;
}

.mobile-action-btn:active,
.mobile-chat-fab:active {
  background: rgba(47, 125, 79, 0.24);
  color: var(--color-leaf);
}

.mobile-chat-fab {
  position: absolute;
  left: 16px;
  bottom: 16px;
  z-index: 30;
}

.mobile-nav-wrap {
  position: absolute;
  right: 16px;
  bottom: 16px;
  z-index: 35;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
}

.mobile-nav-menu {
  width: min(72vw, 260px);
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 10px;
  border: 1px solid rgba(167, 227, 178, 0.1);
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.94), rgba(18, 20, 24, 0.98));
  box-shadow: 0 18px 38px rgba(6, 8, 10, 0.38);
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

.mobile-nav-fab {
  width: 58px;
  height: 58px;
  border-radius: 999px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background:
    radial-gradient(circle at top, rgba(167, 227, 178, 0.18), transparent 62%),
    linear-gradient(180deg, rgba(42, 47, 54, 0.98), rgba(19, 22, 27, 0.98));
  box-shadow: 0 18px 36px rgba(6, 8, 10, 0.38);
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 5px;
  color: var(--color-ink);
}

.mobile-nav-fab span {
  width: 22px;
  height: 2px;
  border-radius: 999px;
  background: currentColor;
  transition: transform 0.18s ease, opacity 0.18s ease;
}

.mobile-nav-fab--open span:nth-child(1) {
  transform: translateY(7px) rotate(45deg);
}

.mobile-nav-fab--open span:nth-child(2) {
  opacity: 0;
}

.mobile-nav-fab--open span:nth-child(3) {
  transform: translateY(-7px) rotate(-45deg);
}

/* Overlays */
.mobile-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  background:
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 36, 23, 0.98));
}

.mobile-overlay-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
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

.mobile-overlay-close {
  background: none;
  border: none;
  color: rgba(154, 166, 154, 0.78);
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.mobile-overlay-close:active {
  color: var(--color-ink);
  background: rgba(47, 125, 79, 0.18);
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
  transition: transform 0.3s ease;
}

.mobile-slide-enter-from,
.mobile-slide-leave-to {
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
}

.mobile-chat-overlay .chat-header {
  display: none;
}

.mobile-chat-overlay .chat-panel {
  background:
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 20, 17, 0.98));
}
</style>
