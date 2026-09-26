<script setup lang="ts">
import { useTheme, type ThemePreference } from '../composables/useTheme'

const { preference, setPreference } = useTheme()
const options: { value: ThemePreference; label: string }[] = [
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
  { value: 'system', label: 'System' },
]
</script>

<template>
  <div class="theme-selector" role="group" aria-label="Color theme">
    <button v-for="option in options" :key="option.value" type="button"
      :aria-pressed="preference === option.value" @click="setPreference(option.value)">
      <svg v-if="option.value === 'light'" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="3.5" /><path d="M10 1v2m0 14v2M1 10h2m14 0h2M3.6 3.6 5 5m10 10 1.4 1.4M3.6 16.4 5 15M15 5l1.4-1.4" /></svg>
      <svg v-else-if="option.value === 'dark'" viewBox="0 0 20 20" aria-hidden="true"><path d="M16.8 12.4A7.1 7.1 0 0 1 7.6 3.2a7.2 7.2 0 1 0 9.2 9.2Z" /></svg>
      <svg v-else viewBox="0 0 20 20" aria-hidden="true"><rect x="2" y="3" width="16" height="11" rx="2" /><path d="M10 14v3m-4 0h8" /></svg>
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.theme-selector { display: flex; gap: 3px; padding: 4px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--surface-1); }
button { display: flex; align-items: center; justify-content: center; flex: 1; gap: 5px; min-height: 34px; padding: 5px 8px; border: 0; border-radius: 8px; color: var(--color-muted); background: transparent; font: 500 0.73rem var(--font-body); cursor: pointer; }
button:hover { background: var(--surface-2); color: var(--color-ink); }
button[aria-pressed='true'] { background: var(--accent-soft); color: var(--color-leaf); }
svg { width: 15px; height: 15px; fill: none; stroke: currentColor; stroke-width: 1.4; stroke-linecap: round; stroke-linejoin: round; }
@media (max-width: 768px) { button { min-height: 44px; } }
</style>
