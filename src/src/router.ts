import { createRouter, createWebHashHistory } from 'vue-router'
import MapView from './views/MapView.vue'

// Only the map is in the entry chunk. The dashboards and the species screen
// pull in the Trilogy studio components (charts, the Monaco editor), which
// are several MB the map never needs; a phone opening the map pays for them
// only when it navigates there. See docs/MOBILE_LOAD_BUDGET.md.
export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'map', component: MapView },
    { path: '/summary', name: 'summary', component: () => import('./views/SummaryView.vue') },
    { path: '/species', name: 'species', component: () => import('./views/SpeciesView.vue') },
    { path: '/info', name: 'info', component: () => import('./views/InfoView.vue') },
    { path: '/profile', name: 'profile', component: () => import('./views/ProfileView.vue') },
    { path: '/submit', name: 'submit', component: () => import('./views/SubmitView.vue') },
    { path: '/contributions', name: 'contributions', component: () => import('./views/ContributionsView.vue') },
  ],
})
