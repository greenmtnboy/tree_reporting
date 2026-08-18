<template>
  <div class="profile-view">
    <div class="profile-card">
      <h1 class="profile-title">Profile</h1>

      <section class="profile-status">
        <template v-if="!firebaseAvailable">
          <p>Profile features are unavailable right now. Authentication services couldn't be reached.</p>
        </template>
        <template v-else-if="!authReady">
          <p class="muted">Loading...</p>
        </template>
        <template v-else-if="!user">
          <p>You're not signed in.</p>
          <p class="muted">
            Use Google to keep your contributions recoverable across refreshes,
            browser resets, and other devices. Anonymous mode is still available
            if you want the fastest possible path to submit.
          </p>
          <div class="profile-actions">
            <button class="btn-google" :disabled="pending" @click="handleGoogleSignIn">
              {{ googleButtonLabel }}
            </button>
            <button class="btn-secondary" :disabled="pending" @click="handleAnonymousSignIn">
              {{ pending ? 'Signing in...' : 'Continue anonymously' }}
            </button>
          </div>
          <p v-if="authError" class="error-text">Sign-in failed: {{ authError.message }}</p>
        </template>
        <template v-else>
          <p>{{ signedInSummary }}</p>
          <p v-if="isAnonymous" class="muted">
            Link Google before clearing this browser if you want to keep this exact account ID.
          </p>
          <dl class="kv">
            <template v-if="user.displayName">
              <dt>Name</dt>
              <dd>{{ user.displayName }}</dd>
            </template>
            <template v-if="user.email">
              <dt>Email</dt>
              <dd>{{ user.email }}</dd>
            </template>
            <dt>Account ID</dt>
            <dd><code>{{ user.uid }}</code></dd>
          </dl>
          <router-link :to="{ name: 'contributions' }" class="badge-strip">
            <template v-if="contributionsLoading">
              <span class="muted">Loading badges…</span>
            </template>
            <template v-else-if="earnedBadges.length">
              <span class="badge-strip__count">{{ earnedBadges.length }} / {{ totalBadges }}</span>
              <span class="badge-strip__label">badges</span>
              <span class="badge-strip__emojis">
                <span
                  v-for="b in earnedBadges"
                  :key="b.id"
                  class="badge-strip__emoji"
                  :title="`${b.title} — ${b.description}`"
                >{{ b.emoji }}</span>
              </span>
            </template>
            <template v-else>
              <span class="muted">No badges yet — submit a tree or check in to start earning.</span>
            </template>
          </router-link>
          <div class="profile-actions">
            <router-link class="btn-primary" :to="{ name: 'contributions' }">
              My contributions
            </router-link>
            <button
              v-if="isAnonymous"
              class="btn-google"
              :disabled="pending"
              @click="handleGoogleSignIn"
            >
              {{ googleButtonLabel }}
            </button>
            <button class="btn-secondary" :disabled="pending" @click="handleSignOut">
              Sign out
            </button>
          </div>
          <p v-if="authError" class="error-text">Sign-in failed: {{ authError.message }}</p>
        </template>
      </section>

      <details class="privacy-section">
        <summary>Privacy &amp; contribution terms</summary>
        <div class="privacy-body">
          <h2>What we collect</h2>
          <ul>
            <li>A pseudonymous account ID, plus optional Google profile info if you link it</li>
            <li>Tree check-ins you record (location + timestamp)</li>
            <li>Photos you submit, with the location you confirm</li>
            <li>
              Aggregated page-view counts via
              <a href="https://www.goatcounter.com/" target="_blank" rel="noopener">GoatCounter</a>,
              which does not use cookies or track individuals
            </li>
          </ul>

          <h2>Photo submissions are public-domain contributions</h2>
          <p>
            By submitting a photo, you release it under
            <a href="https://creativecommons.org/publicdomain/zero/1.0/" target="_blank" rel="noopener">
              CC0 (public domain)
            </a>. This means the photo can be displayed on the map, included in
            data exports, and reused by anyone for any purpose, permanently.
          </p>
          <p>
            Before upload, photos are resized in your browser and EXIF metadata
            (including any GPS embedded by your camera) is stripped. Only the
            location you confirm on the pin is recorded.
          </p>

          <h2>Deleting your account</h2>
          <p>
            You can sign out any time; your local session is cleared. To fully
            delete your account data, email
            <a href="mailto:trilogy.data.community@gmail.com">trilogy.data.community@gmail.com</a>.
            When you request deletion:
          </p>
          <ul>
            <li>Your account record and check-in history are removed.</li>
            <li>Submission records linking photos to you are removed.</li>
            <li>
              Photos themselves remain publicly available under CC0, but no
              longer associated with your identity.
            </li>
          </ul>

          <h2>What we don't do</h2>
          <ul>
            <li>We don't sell or share your data.</li>
            <li>We don't email you unless you explicitly contact us first.</li>
          </ul>
        </div>
      </details>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useAuth } from '../composables/useAuth'
import { firebaseAvailable } from '../lib/firebase'
import { useMyContributions } from '../composables/useSubmissions'
import {
  ACHIEVEMENTS,
  evaluateAchievements,
  toAchievementCheckin,
  toAchievementSubmission,
} from '../lib/achievements'

const {
  user,
  authReady,
  authError,
  isAnonymous,
  redirectingToGoogle,
  signInIfNeeded,
  signInWithGoogle,
  signOut,
} = useAuth()
const pending = ref(false)

const googleButtonLabel = computed(() => {
  if (pending.value && redirectingToGoogle.value) return 'Redirecting to Google...'
  if (pending.value) return isAnonymous.value ? 'Linking Google...' : 'Signing in...'
  return isAnonymous.value ? 'Link Google account' : 'Continue with Google'
})

const signedInSummary = computed(() =>
  isAnonymous.value ? 'Signed in anonymously.' : 'Signed in with Google.',
)

const {
  submissions,
  checkins,
  loading: contributionsLoading,
  refresh: refreshContributions,
} = useMyContributions()

const totalBadges = ACHIEVEMENTS.length

const earnedBadges = computed(() =>
  evaluateAchievements(
    submissions.value.map(toAchievementSubmission),
    checkins.value.map(toAchievementCheckin),
  ).filter((a) => a.earned),
)

onMounted(() => {
  if (user.value) void refreshContributions()
})

watch(user, (u) => {
  if (u) void refreshContributions()
  else {
    submissions.value = []
    checkins.value = []
  }
})

async function handleAnonymousSignIn() {
  pending.value = true
  try {
    await signInIfNeeded()
  } catch {
    /* error already surfaced via authError */
  } finally {
    pending.value = false
  }
}

async function handleGoogleSignIn() {
  pending.value = true
  try {
    await signInWithGoogle()
  } catch {
    /* error already surfaced via authError */
  } finally {
    pending.value = false
  }
}

async function handleSignOut() {
  pending.value = true
  try {
    await signOut()
  } finally {
    pending.value = false
  }
}
</script>

<style scoped>
.profile-view {
  padding: 32px 20px;
  overflow-y: auto;
  max-width: 720px;
  margin: 0 auto;
}

.profile-card {
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.profile-title {
  font-size: 1.6rem;
  font-family: var(--font-display);
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-ink);
  margin: 0;
}

.profile-status {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 20px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
}

.profile-status p {
  margin: 0;
  color: var(--color-ink);
}

.muted {
  color: var(--color-muted);
  font-size: 0.88rem;
}

.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px 12px;
  margin: 4px 0;
}

.kv dt {
  color: var(--color-muted);
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  align-self: center;
}

.kv dd {
  margin: 0;
  font-size: 0.82rem;
  word-break: break-all;
}

.kv code {
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--color-leaf);
}

.badge-strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(167, 227, 178, 0.14);
  background: rgba(28, 31, 36, 0.6);
  text-decoration: none;
  transition: border-color 0.15s, background 0.15s;
}

.badge-strip:hover {
  border-color: var(--color-leaf);
  background: rgba(47, 125, 79, 0.1);
}

.badge-strip__count {
  font-family: var(--font-display);
  font-size: 0.95rem;
  letter-spacing: 0.06em;
  color: var(--color-leaf);
}

.badge-strip__label {
  font-family: var(--font-display);
  font-size: 0.68rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-muted);
}

.badge-strip__emojis {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-left: 4px;
}

.badge-strip__emoji {
  font-size: 1.1rem;
  line-height: 1;
  cursor: help;
}

.profile-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.btn-primary,
.btn-secondary,
.btn-google {
  align-self: flex-start;
  padding: 10px 18px;
  font-family: var(--font-display);
  font-size: 0.8rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  border: 1px solid rgba(167, 227, 178, 0.3);
  cursor: pointer;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}

.btn-primary {
  background: var(--color-leaf);
  color: #0b0f0d;
  border-color: var(--color-leaf);
}

.btn-primary:hover:not(:disabled) {
  background: transparent;
  color: var(--color-leaf);
}

.btn-secondary {
  background: transparent;
  color: var(--color-ink);
}

.btn-secondary:hover:not(:disabled) {
  background: rgba(167, 227, 178, 0.08);
}

.btn-google {
  background: #f3f6ef;
  color: #122014;
  border-color: #f3f6ef;
}

.btn-google:hover:not(:disabled) {
  background: transparent;
  color: #f3f6ef;
}

.btn-primary:disabled,
.btn-secondary:disabled,
.btn-google:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.error-text {
  color: #ff8a8a;
  font-size: 0.82rem;
}

.privacy-section {
  border: 1px solid rgba(167, 227, 178, 0.12);
  background: rgba(28, 31, 36, 0.5);
}

.privacy-section summary {
  padding: 14px 20px;
  font-family: var(--font-display);
  font-size: 0.82rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-moss);
  cursor: pointer;
  list-style: none;
  position: relative;
}

.privacy-section summary::-webkit-details-marker {
  display: none;
}

.privacy-section summary::after {
  content: '+';
  position: absolute;
  right: 20px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-moss);
}

.privacy-section[open] summary::after {
  content: '-';
}

.privacy-body {
  padding: 4px 20px 20px;
  color: rgba(237, 242, 235, 0.88);
  font-size: 0.88rem;
  line-height: 1.55;
}

.privacy-body h2 {
  font-size: 0.82rem;
  font-family: var(--font-display);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-leaf);
  margin: 18px 0 6px;
}

.privacy-body h2:first-child {
  margin-top: 0;
}

.privacy-body p,
.privacy-body ul {
  margin: 0 0 8px;
}

.privacy-body ul {
  padding-left: 22px;
}

.privacy-body a {
  color: var(--color-leaf);
}
</style>
