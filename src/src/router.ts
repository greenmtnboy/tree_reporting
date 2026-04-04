import { createRouter, createWebHashHistory } from 'vue-router'
import MapView from './views/MapView.vue'
import InfoView from './views/InfoView.vue'
import SummaryView from './views/SummaryView.vue'
import SpeciesView from './views/SpeciesView.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'map', component: MapView },
    { path: '/summary', name: 'summary', component: SummaryView },
    { path: '/species', name: 'species', component: SpeciesView },
    { path: '/info', name: 'info', component: InfoView },
  ],
})
