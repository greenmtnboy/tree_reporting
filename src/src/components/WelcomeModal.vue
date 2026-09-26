<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div v-if="visible" class="welcome-overlay" @click.self="dismiss">
        <div class="welcome-modal" role="dialog" aria-modal="true" aria-labelledby="welcome-title">
          <div class="welcome-header">
            <h2 id="welcome-title">Welcome to Urban Trees</h2>
            <button class="welcome-close" @click="dismiss" aria-label="Close">&times;</button>
          </div>
          <div class="welcome-body">
                        <p>
              We're glad you're here!

            </p>
            <p>
              The Urban Tree Explorer is an interactive map of <strong>individual trees</strong> across multiple cities.
              Navigate across your favorite city to discover the diversity and distribution of trees 
              in it's urban forest.

            </p>
            <p>
              You can navigate the map freely, or optionally use the
              <strong>AI agent assistant</strong> in the chat panel — ask it questions, request
              filters, or let it guide you through interesting patterns in the data.
            </p>
            <p>
              Tree and city level summary reports, accessible on the left, will
              give you a quick overview if you want to see a summarized form.
            </p>
            <p>
              You can also navigate by landmark in most cities - this is a great
              way to explore trees around your favorite parks, buildings,
              monuments, and other points of interest.
            </p>
            <div class="welcome-section">
              <h3>Agent API Access</h3>
              <p>
                You can <strong>bring your own API token</strong> for unlimited use, or try a limited
                use<strong> demo token</strong>.
              </p>
            </div>

            <div class="welcome-section">
              <h3>Data Sources</h3>
              <p>
                Tree inventories come from each city's open data portal, enriched with species
                metadata and landmarks. See the full list on the
                <router-link to="/info#data-sources" @click="dismiss">data sources page</router-link>.
              </p>
              <p class="welcome-disclaimer">
                Species metadata may contain inaccuracies. Corrections are welcome!
              </p>
            </div>

            <p class="welcome-fun">Have fun exploring!</p>
          </div>
          <div class="welcome-footer">
            <label class="welcome-dismiss-label">
              <input type="checkbox" v-model="dontShowAgain" />
              Don't show this again
            </label>
            <button class="welcome-btn" @click="dismiss">Get Started</button>
          </div>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

const STORAGE_KEY = 'sf_trees_welcome_dismissed'

const visible = ref(false)
const dontShowAgain = ref(true)

onMounted(() => {
  if (!localStorage.getItem(STORAGE_KEY)) {
    visible.value = true
  }
})

function dismiss() {
  visible.value = false
  if (dontShowAgain.value) {
    localStorage.setItem(STORAGE_KEY, '1')
  }
}
</script>

<style scoped>
.welcome-overlay {
  position: fixed;
  inset: 0;
  background: rgba(6, 10, 14, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}

.welcome-modal {
  width: 100%;
  max-width: 520px;
  max-height: 90vh;
  overflow-y: auto;
  background:
    linear-gradient(180deg, rgba(var(--surface-rgb), 0.98), rgba(var(--surface-rgb), 0.98));
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  box-shadow: 0 24px 56px rgba(6, 8, 10, 0.55);
  display: flex;
  flex-direction: column;
}

.welcome-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(var(--accent-rgb), 0.1);
}

.welcome-header h2 {
  font-family: var(--font-display);
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--color-ink);
  margin: 0;
}

.welcome-close {
  background: transparent;
  border: 1px solid rgba(var(--accent-rgb), 0.2);
  color: var(--color-ink);
  width: 30px;
  height: 30px;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.welcome-close:hover {
  background: rgba(var(--accent-rgb), 0.08);
  border-color: rgba(var(--accent-rgb), 0.4);
}

.welcome-body {
  padding: 16px 20px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: rgba(var(--ink-rgb), 0.82);
}

.welcome-body p {
  margin-bottom: 12px;
}

.welcome-body strong {
  color: var(--color-leaf);
  font-weight: 600;
}

.welcome-section {
  margin: 16px 0;
  padding: 12px 14px;
  background: rgba(var(--accent-rgb), 0.08);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  border-left: 3px solid var(--color-moss);
}

.welcome-section h3 {
  font-family: var(--font-display);
  font-size: 0.72rem;
  font-weight: 600;
  color: var(--color-leaf);
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.welcome-section p {
  margin-bottom: 0;
}

.welcome-section p + p {
  margin-top: 8px;
}

.welcome-section ul {
  list-style: none;
  padding: 0;
  margin: 4px 0 8px;
}

.welcome-section ul li {
  padding: 2px 0;
}

.welcome-section ul li::before {
  content: '• ';
  color: var(--color-moss);
}

.welcome-section a {
  color: var(--color-leaf);
  text-decoration: none;
}

.welcome-section a:hover {
  text-decoration: underline;
}

.welcome-disclaimer {
  font-size: 0.8rem;
  color: var(--color-muted);
  font-style: italic;
  margin-top: 8px;
}

.welcome-fun {
  font-family: var(--font-display);
  font-size: 0.95rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: var(--color-leaf);
  text-align: center;
  margin-top: 16px;
  margin-bottom: 0 !important;
}

.welcome-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 20px;
  border-top: 1px solid rgba(var(--accent-rgb), 0.1);
}

.welcome-dismiss-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: var(--color-muted);
  cursor: pointer;
}

.welcome-dismiss-label input[type="checkbox"] {
  accent-color: var(--color-moss);
}

.welcome-btn {
  border-radius: 8px;
  background: var(--color-leaf);
  color: var(--color-on-accent);
  border: 1px solid rgba(var(--accent-rgb), 0.3);
  padding: 8px 22px;
  font-family: var(--font-display);
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.welcome-btn:hover {
  background: var(--color-moss);
  border-color: rgba(var(--accent-rgb), 0.5);
}

.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity 0.3s ease;
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}

.modal-fade-enter-active .welcome-modal,
.modal-fade-leave-active .welcome-modal {
  transition: transform 0.3s ease;
}

.modal-fade-enter-from .welcome-modal {
  transform: scale(0.95) translateY(10px);
}

.modal-fade-leave-to .welcome-modal {
  transform: scale(0.95) translateY(10px);
}
</style>
