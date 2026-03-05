<template>
  <div class="mobile-layout">
    <div class="mobile-map-container">
      <TreeMap simplified />
    </div>

    <div v-if="!activePanel" class="mobile-bottom-bar">
      <button class="mobile-bar-btn" @click="openPanel('search')">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        Search
      </button>
      <button class="mobile-bar-btn" @click="openPanel('chat')">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        Chat
      </button>
      <button class="mobile-bar-btn" @click="openPanel('info')">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
        Info
      </button>
    </div>

    <transition name="mobile-slide">
      <div v-if="activePanel === 'search'" class="mobile-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">Search Landmarks</span>
          <button class="mobile-overlay-close" @click="activePanel = null">&times;</button>
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
      <div v-if="activePanel === 'chat'" class="mobile-overlay mobile-chat-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">Tree Assistant</span>
          <button class="mobile-overlay-close" @click="activePanel = null">&times;</button>
        </div>
        <ChatPanel />
      </div>
    </transition>

    <transition name="mobile-slide">
      <div v-if="activePanel === 'info'" class="mobile-overlay">
        <div class="mobile-overlay-header">
          <span class="mobile-overlay-title">About</span>
          <button class="mobile-overlay-close" @click="activePanel = null">&times;</button>
        </div>
        <div class="mobile-overlay-body mobile-info-body">
          <InfoView />
        </div>
      </div>
    </transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import TreeMap from './TreeMap.vue'
import ChatPanel from './ChatPanel.vue'
import InfoView from '../views/InfoView.vue'
import { useLandmarkData } from '../composables/useLandmarkData'
import { useFlyTo } from '../composables/useFlyTo'
import type { Landmark } from '../types'

const activePanel = ref<'search' | 'chat' | 'info' | null>(null)
const search = ref('')

const { landmarks, loading: landmarkLoading } = useLandmarkData()
const { flyTo } = useFlyTo()

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return landmarks.value
  return landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
})

function openPanel(panel: 'search' | 'chat' | 'info') {
  activePanel.value = panel
}

function handleLandmarkClick(lm: Landmark) {
  flyTo({ lng: lm.lng, lat: lm.lat, zoom: 16, label: lm.name })
  activePanel.value = null
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

.mobile-map-container {
  flex: 1;
  position: relative;
  overflow: hidden;
}

/* Bottom bar */
.mobile-bottom-bar {
  display: flex;
  height: 56px;
  background: #16213e;
  border-top: 1px solid #0f3460;
  z-index: 20;
}

.mobile-bar-btn {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: none;
  border: none;
  color: #a0a0c0;
  font-size: 0.9rem;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.mobile-bar-btn + .mobile-bar-btn {
  border-left: 1px solid #0f3460;
}

.mobile-bar-btn:active {
  background: rgba(15, 52, 96, 0.6);
  color: #4fc3f7;
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
  background: #1a1a2e;
}

.mobile-overlay-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  background: #16213e;
  border-bottom: 1px solid #0f3460;
  min-height: 52px;
}

.mobile-overlay-title {
  font-size: 1rem;
  font-weight: 600;
  color: #e0e0e0;
}

.mobile-overlay-close {
  background: none;
  border: none;
  color: #7a7a9e;
  font-size: 1.5rem;
  line-height: 1;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.mobile-overlay-close:active {
  color: #e0e0e0;
  background: rgba(15, 52, 96, 0.5);
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
  border: 1px solid #0f3460;
  border-radius: 8px;
  background: #16213e;
  color: #e0e0e0;
  font-size: 1rem;
  outline: none;
  transition: border-color 0.15s;
}

.mobile-search-input:focus {
  border-color: #4fc3f7;
}

.mobile-search-input::placeholder {
  color: #555577;
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
  color: #a0a0c0;
  font-size: 0.95rem;
  cursor: pointer;
  transition: background 0.12s, color 0.12s;
  border-bottom: 1px solid rgba(15, 52, 96, 0.3);
}

.mobile-landmark-item:active {
  background: rgba(15, 52, 96, 0.5);
  color: #4fc3f7;
}

.mobile-landmarks-empty {
  padding: 24px 16px;
  font-size: 0.9rem;
  color: #555577;
  font-style: italic;
  text-align: center;
}

/* Info overlay body */
.mobile-info-body {
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
}

/* Slide transition */
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
</style>
