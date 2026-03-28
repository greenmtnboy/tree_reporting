<template>
  <div class="mobile-layout">
    <div class="mobile-map-container">
      <TreeMap simplified />
    </div>

    <div v-if="!activePanel" class="mobile-bottom-bar">
      <button class="mobile-bar-btn" @click="openPanel('landmarks')">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        Landmarks
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
      <div v-if="activePanel === 'landmarks'" class="mobile-overlay">
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

const activePanel = ref<'landmarks' | 'chat' | 'info' | null>(null)
const search = ref('')

const { landmarks, loading: landmarkLoading } = useLandmarkData()
const { flyTo } = useFlyTo()

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return landmarks.value
  return landmarks.value.filter((l) => l.name.toLowerCase().includes(q))
})

function openPanel(panel: 'landmarks' | 'chat' | 'info') {
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
  height: 62px;
  background:
    linear-gradient(180deg, rgba(58, 64, 72, 0.42), rgba(28, 31, 36, 0.96));
  border-top: 1px solid rgba(167, 227, 178, 0.08);
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
  color: rgba(237, 242, 235, 0.76);
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}

.mobile-bar-btn + .mobile-bar-btn {
  border-left: 1px solid rgba(167, 227, 178, 0.08);
}

.mobile-bar-btn:active {
  background: rgba(47, 125, 79, 0.18);
  color: var(--color-leaf);
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
