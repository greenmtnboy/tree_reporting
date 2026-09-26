<template>
  <div class="map-view">
    <div class="map-surface">
      <TreeMap />
      <router-link
        v-if="firebaseAvailable"
        :to="{ name: 'submit' }"
        class="submit-fab"
        aria-label="Submit a tree"
        title="Submit a tree"
      >
        <span aria-hidden="true">+</span>
      </router-link>
    </div>
  </div>
</template>

<script setup lang="ts">
import TreeMap from '../components/TreeMap.vue'
import { firebaseAvailable } from '../lib/firebase'
</script>

<style scoped>
.map-surface {
  position: relative;
  width: 100%;
  height: 100%;
}

.map-view {
  position: relative;
  width: 100%;
  height: 100%;
}

/* Desktop: parked in the top-right control row, immediately left of the compass
   (which is 72px wide at right: 49px) so it clears the bottom-right legend. */
.submit-fab {
  position: absolute;
  right: 129px;
  top: 20px;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  /* Sits quietly alongside the other map controls: an outlined ghost that only
     fills in on hover, so it doesn't shout over the map. */
  background: rgba(var(--surface-rgb), 0.5);
  color: var(--color-leaf);
  border: 1px solid rgba(var(--accent-rgb), 0.42);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-display);
  font-size: 1.8rem;
  line-height: 1;
  text-decoration: none;
  box-shadow: 0 10px 24px rgba(7, 10, 11, 0.24);
  z-index: 20;
  transition: transform 0.15s ease, background 0.15s, color 0.15s, border-color 0.15s;
}

.submit-fab:hover {
  background: var(--color-leaf);
  color: var(--color-on-accent);
  border-color: var(--color-leaf);
  transform: translateY(-1px);
}

/* Mobile keeps the thumb-reachable, solid bottom-right FAB — there's no hover
   there, so the ghost treatment would just make it easy to miss. */
@media (max-width: 768px) {
  .submit-fab {
    top: auto;
    right: 14px;
    bottom: 92px;
    width: 56px;
    height: 56px;
    background: var(--color-leaf);
    color: var(--color-on-accent);
    border-color: var(--color-leaf);
    box-shadow: 0 18px 34px rgba(6, 8, 10, 0.4);
  }
}
</style>
