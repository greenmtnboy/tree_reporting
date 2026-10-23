<template>
  <FieldBackdrop />
  <MobileLayout v-if="isMobile" />
  <template v-else>
    <AppSidebar />
    <div class="main-col">
      <TopBar />
      <main class="main-content">
        <!-- The map stays alive across tabs so returning to it doesn't rebuild
             MapLibre and reload the city; see TreeMap's onActivated. -->
        <router-view v-slot="{ Component }">
          <KeepAlive include="MapView">
            <component :is="Component" />
          </KeepAlive>
        </router-view>
      </main>
    </div>
    <ChatPanel />
  </template>
  <WelcomeModal v-if="route.name !== 'map'" />
</template>

<script setup lang="ts">
import { defineAsyncComponent } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import FieldBackdrop from './components/FieldBackdrop.vue'
import AppSidebar from './components/AppSidebar.vue'
import MobileLayout from './components/MobileLayout.vue'
// The chat brings the LLM client and markdown renderer with it; load it
// beside the map rather than before it. See docs/MOBILE_LOAD_BUDGET.md.
const ChatPanel = defineAsyncComponent(() => import('./components/ChatPanel.vue'))
import TopBar from './components/TopBar.vue'
import WelcomeModal from './components/WelcomeModal.vue'
import { useIsMobile } from './composables/useIsMobile'
import { CITY_CONFIG, useMapData, type CityCode } from './composables/useMapData'
import { useMapLifecycle } from './composables/useMapLifecycle'

const { isMobile } = useIsMobile()
const { selectedCity } = useMapData()
const { activateCity } = useMapLifecycle()
const route = useRoute()
const router = useRouter()

void router.isReady().then(() => {
  const value = route.query.city
  const city = Array.isArray(value) ? value[0] : value
  if (typeof city === 'string' && city in CITY_CONFIG && city !== selectedCity.value) {
    activateCity(city as CityCode)
  }
})
</script>
