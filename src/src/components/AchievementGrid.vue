<template>
  <div class="achievements">
    <div class="achievements-summary">
      <span class="achievements-count">{{ earnedCount }} / {{ achievements.length }}</span>
      <span class="muted">badges earned</span>
    </div>
    <ul class="badge-grid">
      <li
        v-for="a in sorted"
        :key="a.id"
        :class="['badge', { earned: a.earned }]"
        :title="a.description"
      >
        <span class="badge-emoji" aria-hidden="true">{{ a.earned ? a.emoji : '🔒' }}</span>
        <span class="badge-title">
          {{ a.title }}
          <span v-if="isNew(a.id)" class="badge-new">New!</span>
        </span>
        <span class="badge-desc">{{ a.description }}</span>
        <span v-if="!a.earned && a.target > 1" class="badge-progress">
          <span class="badge-progress__bar" :style="{ width: `${(a.progress / a.target) * 100}%` }"></span>
          <span class="badge-progress__label">{{ a.progress }} / {{ a.target }}</span>
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import {
  evaluateAchievements,
  toAchievementCheckin,
  toAchievementSubmission,
} from '../lib/achievements'
import type { Checkin, Submission } from '../composables/useSubmissions'

const props = defineProps<{
  submissions: Submission[]
  checkins: Checkin[]
}>()

const SEEN_KEY = 'treeAchievements.seen'

function readSeen(): Set<string> {
  try {
    const raw = localStorage.getItem(SEEN_KEY)
    return new Set(raw ? (JSON.parse(raw) as string[]) : [])
  } catch {
    return new Set()
  }
}

// Snapshot once so "New!" chips survive the persist below for this visit.
const seenAtLoad = readSeen()

const achievements = computed(() =>
  evaluateAchievements(
    props.submissions.map(toAchievementSubmission),
    props.checkins.map(toAchievementCheckin),
  ),
)

const sorted = computed(() =>
  [...achievements.value].sort((a, b) => Number(b.earned) - Number(a.earned)),
)

const earnedCount = computed(() => achievements.value.filter((a) => a.earned).length)

function isNew(id: string): boolean {
  const a = achievements.value.find((x) => x.id === id)
  return Boolean(a?.earned) && !seenAtLoad.has(id)
}

watch(
  achievements,
  (all) => {
    const earned = all.filter((a) => a.earned).map((a) => a.id)
    if (earned.length === 0) return
    try {
      localStorage.setItem(SEEN_KEY, JSON.stringify(earned))
    } catch {
      // Non-essential — "New!" chips just reappear next visit.
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.achievements {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.achievements-summary {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.achievements-count {
  font-family: var(--font-display);
  font-size: 1.1rem;
  letter-spacing: 0.06em;
  color: var(--color-leaf);
}

.muted {
  color: var(--color-muted);
  font-size: 0.85rem;
}

.badge-grid {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 8px;
}

.badge {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 10px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
  min-height: 108px;
}

.badge:not(.earned) {
  opacity: 0.55;
}

.badge.earned {
  border-color: rgba(167, 227, 178, 0.4);
  background: rgba(47, 125, 79, 0.12);
}

.badge-emoji {
  font-size: 1.5rem;
  line-height: 1;
}

.badge-title {
  font-family: var(--font-display);
  font-size: 0.72rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--color-ink);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.badge-new {
  font-size: 0.6rem;
  padding: 1px 6px;
  background: var(--color-leaf);
  color: #0b0f0d;
  letter-spacing: 0.08em;
}

.badge-desc {
  font-size: 0.72rem;
  color: var(--color-muted);
  line-height: 1.35;
  flex: 1;
}

.badge-progress {
  position: relative;
  height: 14px;
  background: rgba(167, 227, 178, 0.1);
  overflow: hidden;
}

.badge-progress__bar {
  position: absolute;
  inset: 0 auto 0 0;
  background: rgba(109, 168, 123, 0.45);
}

.badge-progress__label {
  position: relative;
  display: block;
  text-align: center;
  font-size: 0.62rem;
  line-height: 14px;
  color: var(--color-ink);
  font-family: var(--font-mono, ui-monospace, monospace);
}
</style>
