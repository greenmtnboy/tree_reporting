import { createApp } from 'vue'
import { ref } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
import './composables/useAuth'
import { useUserSettingsStore } from '@trilogy-data/trilogy-studio-components/stores'
import 'maplibre-gl/dist/maplibre-gl.css'
import '@trilogy-data/trilogy-studio-components/style.css'
import './assets/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)

const userSettingsStore = useUserSettingsStore(pinia as any)
const isMobile = ref(window.matchMedia('(max-width: 768px)').matches)
const mobileQuery = window.matchMedia('(max-width: 768px)')
const onMobileQueryChange = (event: MediaQueryListEvent) => {
  isMobile.value = event.matches
}
mobileQuery.addEventListener('change', onMobileQueryChange)

app.provide('userSettingsStore', userSettingsStore)
app.provide('isMobile', isMobile)

app.use(router).mount('#app')
