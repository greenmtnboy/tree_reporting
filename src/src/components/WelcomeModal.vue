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
              This is an interactive explorer of <strong>urban tree populations</strong> across multiple cities.
              Browse the map to discover the diversity and distribution of trees across the urban forest.
            </p>
            <p>
              You can navigate the map freely, but the experience is best when using the
              <strong>AI agent assistant</strong> in the chat panel — ask it questions, request
              filters, or let it guide you through interesting patterns in the data.
            </p>

            <div class="welcome-section">
              <h3>API Access</h3>
              <p>
                You can <strong>bring your own API token</strong> for unlimited use, or try the
                <strong>demo token</strong> which has a daily spend cap.
              </p>
            </div>

            <div class="welcome-section">
              <h3>Data Sources</h3>
              <ul>
                <li><a href="https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq" target="_blank" rel="noopener">SF Open Data — Street Tree List</a></li>
                <li><a href="https://data.cityofnewyork.us/Environment/2015-Street-Tree-Census-Tree-Data/uvpi-gqnh" target="_blank" rel="noopener">NYC Open Data — Street Tree Census</a></li>
                <li><a href="https://data.boston.gov/dataset/bprd-trees" target="_blank" rel="noopener">City of Boston — Street Trees</a></li>
                <li><a href="https://opendata.paris.fr/explore/dataset/les-arbres/information/" target="_blank" rel="noopener">Paris Open Data — Les Arbres</a></li>
              </ul>
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
    linear-gradient(180deg, rgba(28, 31, 36, 0.98), rgba(15, 20, 17, 0.98));
  border: 1px solid rgba(167, 227, 178, 0.18);
  box-shadow: 0 24px 56px rgba(6, 8, 10, 0.55);
  display: flex;
  flex-direction: column;
}

.welcome-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.1);
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
  border: 1px solid rgba(167, 227, 178, 0.2);
  color: var(--color-ink);
  width: 30px;
  height: 30px;
  font-size: 1rem;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}

.welcome-close:hover {
  background: rgba(167, 227, 178, 0.08);
  border-color: rgba(167, 227, 178, 0.4);
}

.welcome-body {
  padding: 16px 20px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: rgba(237, 242, 235, 0.82);
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
  background: rgba(47, 125, 79, 0.08);
  border: 1px solid rgba(167, 227, 178, 0.12);
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
  border-top: 1px solid rgba(167, 227, 178, 0.1);
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
  background: linear-gradient(180deg, rgba(47, 125, 79, 0.9), rgba(34, 96, 60, 0.9));
  color: var(--color-ink);
  border: 1px solid rgba(167, 227, 178, 0.3);
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
  background: linear-gradient(180deg, rgba(56, 142, 92, 0.95), rgba(40, 108, 68, 0.95));
  border-color: rgba(167, 227, 178, 0.5);
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
