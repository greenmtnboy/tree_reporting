<template>
  <div class="badges">
    <div class="badges-summary">
      <span class="badges-count">{{ earnedCount }} / {{ badges.length }}</span>
      <span class="muted">badges earned</span>
      <strong class="badge-total-points">{{ totalPoints }} points</strong>
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
        <span class="badge-points">{{ a.points }} points</span>
        <span v-if="!a.earned && a.target > 1" class="badge-progress">
          <span class="badge-progress__bar" :style="{ width: `${(a.progress / a.target) * 100}%` }"></span>
          <span class="badge-progress__label">{{ a.progress }} / {{ a.target }}</span>
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { evaluateCityBadges } from '../lib/missions'
import { useAuth } from '../composables/useAuth'
import { computed, watch } from 'vue'
import {
  evaluateBadges,
  toBadgeCheckin,
  toBadgeSubmission,
} from '../lib/badges'
import type { Checkin, Submission } from '../composables/useSubmissions'

const props = defineProps<{
  submissions: Submission[]
  checkins: Checkin[]
}>()

const SEEN_KEY = `treeBadges.seen:${useAuth().user.value?.uid ?? 'guest'}`

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

const badges = computed(() => [
  ...evaluateBadges(
    props.submissions.map(toBadgeSubmission),
    props.checkins.map(toBadgeCheckin),
  ),
  ...evaluateCityBadges(props.checkins).filter(b => b.earned),
])

const sorted = computed(() =>
  [...badges.value].sort((a, b) => Number(b.earned) - Number(a.earned)),
)

const totalPoints = computed(() => badges.value.filter(b => b.earned).reduce((sum, b) => sum + b.points, 0))
const earnedCount = computed(() => badges.value.filter((a) => a.earned).length)

function isNew(id: string): boolean {
  const a = badges.value.find((x) => x.id === id)
  return Boolean(a?.earned) && !seenAtLoad.has(id)
}

watch(
  badges,
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
.badges {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.badges-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

.badges-count {
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
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  background: rgba(var(--surface-rgb), 0.5);
  min-height: 108px;
}

.badge:not(.earned) {
  opacity: 0.55;
}

.badge.earned {
  border-color: rgba(var(--accent-rgb), 0.4);
  background: rgba(var(--accent-rgb), 0.12);
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
  color: var(--color-on-accent);
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
  background: rgba(var(--accent-rgb), 0.1);
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
