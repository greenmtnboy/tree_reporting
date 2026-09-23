<template>
  <div class="contributions-view">
    <div class="contributions-card">
      <header class="contributions-header">
        <h1>My contributions</h1>
        <router-link v-if="firebaseAvailable" :to="{ name: 'submit' }" class="btn-primary">+ Submit a tree</router-link>
      </header>

      <section v-if="!firebaseAvailable" class="contributions-status">
        <p>Contributions are unavailable right now. Sign-in and submission services couldn't be reached.</p>
      </section>
      <section v-else-if="!authReady" class="contributions-status">
        <p class="muted">Loading…</p>
      </section>
      <section v-else-if="!user" class="contributions-status">
        <p>Sign in to see your contributions.</p>
        <router-link :to="{ name: 'profile' }" class="btn-primary">Go to profile</router-link>
      </section>
      <template v-else>
        <section class="section">
          <h2 class="section-title">Achievements</h2>
          <p v-if="loading" class="muted">Loading…</p>
          <AchievementGrid v-else :submissions="submissions" :checkins="checkins" />
        </section>

        <section class="section">
          <h2 class="section-title">Submissions</h2>
          <p v-if="loading" class="muted">Loading…</p>
          <p v-else-if="error" class="error-text">{{ error.message }}</p>
          <p v-else-if="submissions.length === 0" class="muted">
            No photo submissions yet.
          </p>
          <ul v-else class="submission-list">
            <li v-for="s in submissions" :key="s.id" class="submission-item">
              <SubmissionThumbnail :photo-path="s.photoPath" />
              <div class="submission-meta">
                <div class="submission-row">
                  <span class="submission-status" :data-status="s.status">{{ s.status }}</span>
                  <span class="submission-date">{{ formatDate(s.submittedAt) }}</span>
                </div>
                <div class="submission-coords">
                  <code>{{ s.lat.toFixed(5) }}, {{ s.lng.toFixed(5) }}</code>
                </div>
                <div v-if="s.species" class="submission-species">{{ s.species }}</div>
              </div>
            </li>
          </ul>
        </section>

        <section class="section">
          <h2 class="section-title">Check-ins</h2>
          <p v-if="loading" class="muted">Loading…</p>
          <p v-else-if="checkins.length === 0" class="muted">
            No check-ins yet. Open a tree on the map and tap "Check in" — you need to be within 50 m of the tree.
          </p>
          <ul v-else class="checkin-list">
            <li v-for="c in checkins" :key="c.id" class="checkin-item">
              <SubmissionThumbnail v-if="c.photoPath" :photo-path="c.photoPath" />
              <div class="checkin-meta">
                <div class="checkin-row">
                  <span class="checkin-tree"><code>{{ c.treeId }}</code></span>
                  <span class="checkin-date">{{ formatDate(c.at) }}</span>
                </div>
                <div v-if="c.distanceMeters != null" class="checkin-distance">
                  within {{ c.distanceMeters }} m
                </div>
                <div v-if="c.photoReview" class="checkin-row">
                  <span class="submission-status" :data-status="c.photoReview">{{ c.photoReview }}</span>
                  <span class="checkin-distance">{{ PHOTO_REVIEW_LABELS[c.photoReview] }}</span>
                </div>
              </div>
            </li>
          </ul>
        </section>

        <section class="section">
          <h2 class="section-title">Tree reports</h2>
          <p v-if="loading" class="muted">Loading…</p>
          <p v-else-if="modifications.length === 0" class="muted">
            No reports yet. Standing at a tree that's gone or mapped wrong? On your phone, open it on the map and choose "Missing or mapped wrong? Report it".
          </p>
          <ul v-else class="checkin-list" data-testid="modification-list">
            <li v-for="m in modifications" :key="m.id" class="checkin-item">
              <SubmissionThumbnail v-if="m.photoPath" :photo-path="m.photoPath" />
              <div class="checkin-meta">
                <div class="checkin-row">
                  <span class="submission-status" :data-status="m.status">{{ m.status }}</span>
                  <span class="checkin-date">{{ formatDate(m.submittedAt) }}</span>
                </div>
                <div class="checkin-row">
                  <span>{{ describeModification(m) }}</span>
                  <span class="checkin-tree"><code>{{ m.treeId }}</code></span>
                </div>
              </div>
            </li>
          </ul>
        </section>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useAuth } from '../composables/useAuth'
import {
  useMyContributions,
  type PhotoReviewStatus,
  type TreeModification,
} from '../composables/useSubmissions'
import { firebaseAvailable } from '../lib/firebase'
import SubmissionThumbnail from '../components/SubmissionThumbnail.vue'
import AchievementGrid from '../components/AchievementGrid.vue'

const { user, authReady } = useAuth()
const { submissions, checkins, modifications, loading, error, refresh } = useMyContributions()

onMounted(() => {
  if (user.value) void refresh()
})

watch(user, (u) => {
  if (u) void refresh()
  else {
    submissions.value = []
    checkins.value = []
    modifications.value = []
  }
})

const PHOTO_REVIEW_LABELS: Record<PhotoReviewStatus, string> = {
  pending: 'Tree photo awaiting review',
  published: 'Tree photo is on the map',
  rejected: 'Tree photo not published',
}

function describeModification(m: TreeModification): string {
  if (m.kind === 'missing') return 'Reported missing'
  const parts: string[] = []
  if (m.proposedLat != null && m.proposedLng != null) parts.push('location')
  if (m.proposedSpecies) parts.push(`species → ${m.proposedSpecies}`)
  if (m.proposedDbhInches != null) parts.push(`diameter → ${m.proposedDbhInches} in`)
  return parts.length ? `Fix: ${parts.join(', ')}` : 'Suggested fix'
}

function formatDate(d: Date | null): string {
  if (!d) return 'just now'
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}
</script>

<style scoped>
.contributions-view {
  padding: 32px 20px;
  overflow-y: auto;
  max-width: 720px;
  margin: 0 auto;
  height: 100%;
}

.contributions-card {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.contributions-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.1);
  padding-bottom: 12px;
}

.contributions-header h1 {
  font-size: 1.4rem;
  font-family: var(--font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-ink);
  margin: 0;
}

.contributions-status {
  padding: 20px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-title {
  font-family: var(--font-display);
  font-size: 0.8rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-moss);
  margin: 0;
}

.muted {
  color: var(--color-muted);
  font-size: 0.88rem;
}

.error-text {
  color: #ff8a8a;
}

.submission-list,
.checkin-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.submission-item {
  display: flex;
  gap: 12px;
  padding: 10px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
  align-items: center;
}

.submission-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.submission-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.submission-status {
  font-family: var(--font-display);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 2px 8px;
  border: 1px solid rgba(167, 227, 178, 0.2);
}

.submission-status[data-status='pending'] {
  color: var(--color-muted);
}

.submission-status[data-status='published'] {
  color: var(--color-leaf);
  border-color: var(--color-leaf);
}

.submission-status[data-status='rejected'] {
  color: #ff8a8a;
  border-color: #ff8a8a;
}

.submission-date {
  font-size: 0.78rem;
  color: var(--color-muted);
}

.submission-coords code {
  font-size: 0.76rem;
  color: var(--color-leaf);
}

.submission-species {
  font-size: 0.82rem;
  font-style: italic;
  color: rgba(237, 242, 235, 0.78);
}

.checkin-item {
  display: flex;
  gap: 12px;
  align-items: center;
  padding: 10px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
  font-size: 0.85rem;
}

.checkin-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.checkin-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
}

.checkin-date {
  color: var(--color-muted);
  font-size: 0.78rem;
}

.checkin-distance {
  font-size: 0.76rem;
  color: var(--color-leaf);
}

.btn-primary {
  padding: 8px 14px;
  font-family: var(--font-display);
  font-size: 0.74rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  background: var(--color-leaf);
  color: #0b0f0d;
  border: 1px solid var(--color-leaf);
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  transition: background 0.15s, color 0.15s;
}

.btn-primary:hover {
  background: transparent;
  color: var(--color-leaf);
}
</style>
