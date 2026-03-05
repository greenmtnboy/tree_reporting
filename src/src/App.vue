<template>
  <MobileLayout v-if="isMobile" />
  <template v-else>
    <AppSidebar />
    <main class="main-content">
      <router-view />
    </main>
    <ChatPanel />
  </template>
  <WelcomeModal />
</template>

<script setup lang="ts">
import AppSidebar from './components/AppSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import MobileLayout from './components/MobileLayout.vue'
import WelcomeModal from './components/WelcomeModal.vue'
import { useIsMobile } from './composables/useIsMobile'

const { isMobile } = useIsMobile()
</script>
