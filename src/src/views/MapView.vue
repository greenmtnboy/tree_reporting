<template>
  <div class="map-view">
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
</template>

<script setup lang="ts">
import TreeMap from '../components/TreeMap.vue'
import { firebaseAvailable } from '../lib/firebase'
</script>

<style scoped>
.map-view {
  position: relative;
  width: 100%;
  height: 100%;
}

.submit-fab {
  position: absolute;
  right: 20px;
  bottom: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: var(--color-leaf);
  color: #0b0f0d;
  border: 1px solid var(--color-leaf);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-display);
  font-size: 1.8rem;
  line-height: 1;
  text-decoration: none;
  box-shadow: 0 18px 34px rgba(6, 8, 10, 0.4);
  z-index: 20;
  transition: transform 0.15s ease, background 0.15s, color 0.15s;
}

.submit-fab:hover {
  background: transparent;
  color: var(--color-leaf);
  transform: translateY(-1px);
}

@media (max-width: 768px) {
  .submit-fab {
    right: 14px;
    bottom: 92px;
  }
}
</style>
