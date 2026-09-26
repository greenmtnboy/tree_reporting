<template>
  <FieldBackdrop />
  <MobileLayout v-if="isMobile" />
  <template v-else>
    <AppSidebar />
    <div class="main-col">
      <TopBar />
      <main class="main-content">
        <router-view />
      </main>
    </div>
    <ChatPanel />
  </template>
  <WelcomeModal />
</template>

<script setup lang="ts">
import { useRoute, useRouter } from 'vue-router'
import FieldBackdrop from './components/FieldBackdrop.vue'
import AppSidebar from './components/AppSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import MobileLayout from './components/MobileLayout.vue'
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
