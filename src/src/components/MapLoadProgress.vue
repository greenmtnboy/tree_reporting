<template>
  <div class="map-load-progress">
    <div class="map-load-progress-head">
      <LeafSpinner :size="26" />
      <strong>Growing your map</strong>
    </div>
    <ol class="map-load-progress-steps" :aria-label="`Step ${current + 1} of ${steps.length}`">
      <li
        v-for="(step, i) in steps"
        :key="step.key"
        :class="{ done: i < current, active: i === current }"
        :aria-current="i === current ? 'step' : undefined"
      >
        <span class="map-load-progress-mark" aria-hidden="true">
          <svg v-if="i < current" viewBox="0 0 12 12"><path d="M2.5 6.2 5 8.5l4.5-5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </span>
        <span class="map-load-progress-text">
          <span class="map-load-progress-title">{{ step.title }}{{ i === current ? '…' : '' }}</span>
          <em class="map-load-progress-detail">{{ step.detail }}</em>
        </span>
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
import LeafSpinner from './LeafSpinner.vue'
import type { LoadStep } from '../lib/loadProgress'

defineProps<{ steps: readonly LoadStep[]; current: number }>()
</script>

<style scoped>
.map-load-progress {
  display: grid;
  gap: 12px;
  min-width: min(260px, calc(100vw - 64px));
  text-align: left;
}

.map-load-progress-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.map-load-progress-head strong {
  color: var(--color-ink);
  font-size: 0.95rem;
  font-weight: 600;
}

.map-load-progress-steps {
  list-style: none;
  margin: 0;
  padding: 10px 0 0;
  border-top: 1px solid rgba(var(--accent-rgb), 0.14);
  display: grid;
  gap: 8px;
}

.map-load-progress-steps li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  color: rgba(var(--ink-rgb), 0.4);
  transition: color 0.3s ease;
}

.map-load-progress-steps li.done { color: rgba(var(--ink-rgb), 0.62); }
.map-load-progress-steps li.active { color: var(--color-leaf); }

.map-load-progress-text {
  display: grid;
  gap: 1px;
}

.map-load-progress-title {
  font-size: 0.8rem;
  line-height: 1.25;
}

.map-load-progress-steps li.active .map-load-progress-title { font-weight: 600; }

.map-load-progress-detail {
  font-size: 0.7rem;
  line-height: 1.2;
  opacity: 0.8;
}

.map-load-progress-mark {
  width: 14px;
  height: 14px;
  margin-top: 1px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  display: grid;
  place-items: center;
  flex-shrink: 0;
}

.map-load-progress-steps li.done .map-load-progress-mark {
  border-color: var(--color-leaf);
  background: var(--color-leaf);
  color: var(--surface-1, #fff);
}

.map-load-progress-steps li.active .map-load-progress-mark {
  animation: step-pulse 1.4s ease-in-out infinite;
}

.map-load-progress-mark svg { width: 10px; height: 10px; }

@keyframes step-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(var(--accent-rgb), 0.35); }
  50% { box-shadow: 0 0 0 4px rgba(var(--accent-rgb), 0); }
}

@media (prefers-reduced-motion: reduce) {
  .map-load-progress-steps li.active .map-load-progress-mark { animation: none; }
}
</style>
