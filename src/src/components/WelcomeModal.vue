<template>
  <transition name="modal-fade">
    <div v-if="visible" class="welcome-overlay" @click.self="dismiss">
      <div class="welcome-modal">
        <div class="welcome-header">
          <h2>Welcome to SF Tree Explorer</h2>
          <button class="welcome-close" @click="dismiss" aria-label="Close">&times;</button>
        </div>
        <div class="welcome-body">
          <p>
            This is an interactive explorer of <strong>San Francisco's urban tree population</strong>.
            Browse the map to discover the diversity and distribution of trees across the city.
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
              <li><a href="https://data.sfgov.org/City-Infrastructure/DPW-Maintained-Street-Trees/7g6n-5jhi" target="_blank" rel="noopener">SF DPW — Maintained Street Trees</a></li>
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
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}

.welcome-modal {
  background: #16213e;
  border: 1px solid #0f3460;
  border-radius: 12px;
  max-width: 520px;
  width: 100%;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
}

.welcome-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 0;
}

.welcome-header h2 {
  font-size: 1.25rem;
  font-weight: 600;
  color: #4fc3f7;
  margin: 0;
}

.welcome-close {
  background: none;
  border: none;
  color: #7a7a9e;
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  transition: color 0.15s;
}

.welcome-close:hover {
  color: #e0e0e0;
}

.welcome-body {
  padding: 16px 24px;
  font-size: 0.9rem;
  line-height: 1.6;
  color: #c0c0d8;
}

.welcome-body p {
  margin-bottom: 12px;
}

.welcome-body strong {
  color: #e0e0e0;
}

.welcome-section {
  margin: 16px 0;
  padding: 12px 16px;
  background: rgba(15, 52, 96, 0.3);
  border-radius: 8px;
  border-left: 3px solid #4fc3f7;
}

.welcome-section h3 {
  font-size: 0.85rem;
  font-weight: 600;
  color: #4fc3f7;
  margin-bottom: 6px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
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
  color: #4fc3f7;
}

.welcome-section a {
  color: #4fc3f7;
  text-decoration: none;
}

.welcome-section a:hover {
  text-decoration: underline;
}

.welcome-disclaimer {
  font-size: 0.8rem;
  color: #7a7a9e;
  font-style: italic;
  margin-top: 8px;
}

.welcome-fun {
  font-size: 1rem;
  font-weight: 500;
  color: #4fc3f7;
  text-align: center;
  margin-top: 16px;
  margin-bottom: 0 !important;
}

.welcome-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 24px 20px;
  border-top: 1px solid #0f3460;
}

.welcome-dismiss-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.8rem;
  color: #7a7a9e;
  cursor: pointer;
}

.welcome-dismiss-label input[type="checkbox"] {
  accent-color: #4fc3f7;
}

.welcome-btn {
  background: #4fc3f7;
  color: #1a1a2e;
  border: none;
  padding: 8px 24px;
  border-radius: 6px;
  font-size: 0.9rem;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}

.welcome-btn:hover {
  background: #81d4fa;
}

/* Transition */
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
