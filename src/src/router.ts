import { createRouter, createWebHashHistory } from 'vue-router'
import MapView from './views/MapView.vue'
import InfoView from './views/InfoView.vue'
import SummaryView from './views/SummaryView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'map', component: MapView },
    { path: '/summary', name: 'summary', component: SummaryView },
    { path: '/info', name: 'info', component: InfoView },
  ],
})
