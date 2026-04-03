<template>
  <aside class="chat-panel">
    <div class="chat-header">
      <span>Tree Assistant</span>
      <div class="chat-header-actions">
        <button
          v-if="isConfigured && messages.length"
          class="chat-clear-btn"
          @click="clearMessages"
          title="Clear chat"
        >
          Clear
        </button>
        <button
          v-if="isConfigured && !showSettings"
          class="chat-info-btn"
          :class="{ 'chat-info-btn--active': showInfo }"
          @click="showInfo = !showInfo"
          title="Available data fields"
        >
          &#9432;
        </button>
        <button
          v-if="isConfigured && !showSettings"
          class="chat-gear-btn"
          @click="openSettings"
          title="Manage connection"
        >
          &#9881;
        </button>
      </div>
    </div>

    <div v-if="showInfo && isConfigured && !showSettings" class="chat-info-panel">
      <p class="chat-info-heading">Available Data Fields</p>
      <p>I can tell you about any of the following data points in the tree dataset.</p>
      <p class="chat-info-section">Tree Fields</p>
      <ul class="chat-info-list">
        <li><code>tree_id</code> - unique identifier</li>
        <li><code>tree_name</code> - e.g. "Swamp Myrtle"</li>
        <li><code>species</code> - full species string</li>
        <li><code>plant_date</code> - date planted (MM/DD/YYYY)</li>
        <li><code>latitude</code> / <code>longitude</code></li>
        <li><code>diameter_at_breast_height</code> - trunk diameter (inches)</li>
        <li><code>native_ecoregions</code> - list of ecoregion ids where the species is native</li>
        <li><code>is_evergreen</code> - bool</li>
        <li><code>mature_height_min_ft</code> / <code>mature_height_max_ft</code></li>
        <li><code>canopy_spread_min_ft</code> / <code>canopy_spread_max_ft</code></li>
        <li><code>growth_rate</code> - slow | moderate | fast</li>
        <li><code>lifespan_min_years</code> / <code>lifespan_max_years</code></li>
        <li><code>drought_tolerance</code> - low | moderate | high</li>
        <li><code>water_needs</code> - low | moderate | high</li>
        <li><code>sun_exposure</code> - array of light tolerances</li>
        <li><code>bloom_months</code> - array of month numbers</li>
        <li><code>wildlife_value</code> - low | moderate | high</li>
        <li><code>fire_risk</code> - low | moderate | high</li>
        <li><code>tree_form</code> - broadleaf | conifer | palm | columnar | ornamental | spreading | weeping | multi_trunk | default</li>
        <li><code>description</code> - short ecology and urban planting note</li>
      </ul>
    </div>

    <div v-if="showSettings" class="chat-setup">
      <p class="chat-setup-title">Manage Connection</p>
      <p class="chat-setup-sub">Current: <strong>{{ PROVIDER_LABELS[providerType] ?? providerType }}</strong></p>
      <label class="chat-setup-label">Provider</label>
      <select v-model="typeInput" class="chat-setup-select">
        <option v-for="p in PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
      </select>
      <template v-if="typeInput !== 'demo'">
        <label class="chat-setup-label">API Key</label>
        <input
          v-model="keyInput"
          type="password"
          class="chat-setup-input"
          :placeholder="KEY_PLACEHOLDERS[typeInput] ?? 'API key...'"
          @keydown.enter="saveSettings"
        />
      </template>
      <div v-else class="chat-demo-note">
        Limited to a small number of messages per IP.
      </div>
      <div class="chat-setup-actions">
        <button class="chat-btn-primary" @click="saveSettings" :disabled="!canSaveSettings">Save</button>
        <button class="chat-btn-secondary" @click="showSettings = false">Cancel</button>
      </div>
      <button class="chat-btn-danger" @click="handleDelete">Delete Connection</button>
    </div>

    <div v-else-if="!isConfigured" class="chat-setup">
      <p class="chat-setup-title">Connect an AI backend</p>
      <label class="chat-setup-label">Provider</label>
      <select v-model="typeInput" class="chat-setup-select">
        <option v-for="p in PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
      </select>
      <template v-if="typeInput !== 'demo'">
        <label class="chat-setup-label">API Key</label>
        <input
          v-model="keyInput"
          type="password"
          class="chat-setup-input"
          :placeholder="KEY_PLACEHOLDERS[typeInput] ?? 'API key...'"
          @keydown.enter="saveSetup"
        />
      </template>
      <div v-else class="chat-demo-note">
        Try without an API key. Limited to a small number of messages per IP.
      </div>
      <button class="chat-btn-primary" @click="saveSetup" :disabled="!canSaveSetup">
        Start Chatting
      </button>
    </div>

    <div v-else class="chat-messages" ref="messagesContainer">
      <div v-if="messages.length === 0" class="chat-empty">
        <template v-if="!activeDataReady">
          {{ isSummaryScreen || isSpeciesScreen ? 'Loading analytics...' : 'Loading tree data...' }}
        </template>
        <template v-else>
          {{ emptyStateText }}
          <div class="chat-suggestions">
            <button
              v-for="suggestion in suggestions"
              :key="suggestion"
              class="chat-suggestion"
              :disabled="inputDisabled"
              @click="injectSuggestion(suggestion)"
            >{{ suggestion }}</button>
          </div>
        </template>
      </div>
      <div v-for="(msg, i) in messages" :key="i" :class="['chat-msg', `chat-msg--${msg.role}`]">
        <div v-if="!msg.isLoading && !msg.content && msg.toolCalls?.length" class="chat-tool-pills">
          <span v-for="tc in msg.toolCalls" :key="tc.id" class="chat-tool-pill">{{ tc.name }}</span>
        </div>
        <div v-else class="chat-msg-content">
          <div v-if="msg.isLoading" class="chat-loading">
            <span class="chat-loading-spinner"></span>
            {{ thinkingPhrase }}
          </div>
          <template v-else>
            <MarkdownRenderer v-if="msg.content" :markdown="msg.content" />
            <div v-if="msg.toolCalls?.length" class="chat-tool-pills chat-tool-pills--inline">
              <span v-for="tc in msg.toolCalls" :key="tc.id" class="chat-tool-pill">{{ tc.name }}</span>
            </div>
          </template>
        </div>
      </div>
    </div>

    <div v-if="isConfigured && !showSettings" class="chat-input-area">
        <input
          v-model="userInput"
          type="text"
          :placeholder="inputPlaceholder"
          @keydown.enter="handleSend"
          :disabled="inputDisabled"
        />
      <span class="send-btn-wrapper">
        <button @click="handleSend" :disabled="inputDisabled || !userInput.trim()">Send</button>
        <span v-if="sendTooltip" class="send-tooltip">{{ sendTooltip }}</span>
      </span>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { MarkdownRenderer } from '@trilogy-data/trilogy-studio-components/dashboard'
import { useChat } from '../composables/useChat'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapIntro } from '../composables/useMapIntro'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import { THINKING_PHRASES } from '../constants/loadingPhrases'

const PROVIDERS = [
  { value: 'demo', label: 'Demo (limited messages)' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'google', label: 'Google' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'openrouter', label: 'OpenRouter' },
]

const PROVIDER_LABELS: Record<string, string> = Object.fromEntries(PROVIDERS.map((p) => [p.value, p.label]))

const KEY_PLACEHOLDERS: Record<string, string> = {
  anthropic: 'sk-ant-...',
  openai: 'sk-...',
  google: 'AIza...',
  openrouter: 'sk-or-...',
}

const MAP_SUGGESTIONS = [
  'What can you do?',
  'What is the most common type of tree?',
  'Show me trees in bloom right now!',
  'Where is the biggest tree?',
]

const SUMMARY_SUGGESTIONS = [
  'Filter the charts to local native trees',
  'Show only trees outside the hardiness zone',
  'What share of the city is concentrated in the top 5 species?',
  'Clear the analytics filters',
]

const SPECIES_SUGGESTIONS = [
  'Set the genus to Quercus',
  'Filter this page to Acer rubrum',
  'What does the current species view show?',
  'Clear the species filter but keep the genus',
]

const { messages, isLoading, isConfigured, providerType, setConnection, deleteConnection, sendMessage, clearMessages } = useChat()
const { ready: dbReady } = useDuckDB()
const { introComplete } = useMapIntro()
const { ready: summaryReady, initialize: initializeSummary } = useSummaryDashboardExecution()
const route = useRoute()

const userInput = ref('')
const keyInput = ref('')
const typeInput = ref('demo')
const showSettings = ref(false)
const showInfo = ref(false)
const messagesContainer = ref<HTMLDivElement>()

const thinkingPhrase = ref(THINKING_PHRASES[0])
let thinkingInterval: ReturnType<typeof setInterval> | null = null

watch(isLoading, (loading) => {
  if (loading) {
    let idx = Math.floor(Math.random() * THINKING_PHRASES.length)
    thinkingPhrase.value = THINKING_PHRASES[idx]
    thinkingInterval = setInterval(() => {
      idx = (idx + 1) % THINKING_PHRASES.length
      thinkingPhrase.value = THINKING_PHRASES[idx]
    }, 2500)
  } else if (thinkingInterval != null) {
    clearInterval(thinkingInterval)
    thinkingInterval = null
  }
})

onUnmounted(() => {
  if (thinkingInterval != null) clearInterval(thinkingInterval)
})

const isMapScreen = computed(() => route.name === 'map')
const isSummaryScreen = computed(() => route.name === 'summary')
const isSpeciesScreen = computed(() => route.name === 'species')

watch(
  () => isSummaryScreen.value || isSpeciesScreen.value,
  (analyticsScreen) => {
    if (analyticsScreen && !summaryReady.value) {
      void initializeSummary()
    }
  },
  { immediate: true },
)

const activeDataReady = computed(() =>
  isSummaryScreen.value || isSpeciesScreen.value ? summaryReady.value : dbReady.value,
)

const inputDisabled = computed(() =>
  isLoading.value || !activeDataReady.value || (isMapScreen.value && !introComplete.value),
)

const suggestions = computed(() =>
  route.name === 'summary'
    ? SUMMARY_SUGGESTIONS
    : route.name === 'species'
      ? SPECIES_SUGGESTIONS
      : MAP_SUGGESTIONS,
)

const emptyStateText = computed(() =>
  route.name === 'summary'
    ? 'Ask me to explain or filter the analytics. Try:'
    : route.name === 'species'
      ? 'Ask me to inspect or change the species explorer. Try:'
    : 'Ask me about city trees. Try:',
)

const inputPlaceholder = computed(() =>
  !activeDataReady.value
    ? isSummaryScreen.value || isSpeciesScreen.value
      ? 'Loading analytics...'
      : 'Loading data...'
    : route.name === 'summary'
      ? 'Ask about analytics...'
      : route.name === 'species'
        ? 'Ask about the species explorer...'
      : 'Ask about trees...',
)

const sendTooltip = computed(() => {
  if (isMapScreen.value && !introComplete.value) return 'Map is loading...'
  if (isLoading.value) return 'Waiting for response...'
  if (!activeDataReady.value) {
    return isSummaryScreen.value || isSpeciesScreen.value
      ? 'Analytics are still loading'
      : 'Tree data is still loading'
  }
  if (!userInput.value.trim()) return 'Type a message to send'
  return ''
})

const canSaveSetup = computed(() =>
  typeInput.value === 'demo' ? true : !!keyInput.value.trim(),
)

const canSaveSettings = computed(() =>
  typeInput.value === 'demo' ? true : !!keyInput.value.trim(),
)

function openSettings() {
  typeInput.value = providerType.value || 'anthropic'
  keyInput.value = ''
  showSettings.value = true
}

function saveSetup() {
  if (!canSaveSetup.value) return
  setConnection(typeInput.value, typeInput.value === 'demo' ? '' : keyInput.value.trim())
  keyInput.value = ''
}

function saveSettings() {
  if (!canSaveSettings.value) return
  setConnection(typeInput.value, typeInput.value === 'demo' ? '' : keyInput.value.trim())
  keyInput.value = ''
  showSettings.value = false
}

function handleDelete() {
  deleteConnection()
  typeInput.value = 'demo'
  keyInput.value = ''
  showSettings.value = false
}

async function injectSuggestion(text: string) {
  if (inputDisabled.value) return
  await sendMessage(text)
}

async function handleSend() {
  const text = userInput.value.trim()
  if (!text || inputDisabled.value) return
  userInput.value = ''
  await sendMessage(text)
}

async function scrollToBottom() {
  await nextTick()
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

watch(() => messages.value.length, scrollToBottom)
watch(
  () => messages.value[messages.value.length - 1]?.content,
  scrollToBottom,
)
</script>

<style scoped>
.chat-panel {
  width: 360px;
  min-width: 360px;
  height: 100%;
  position: relative;
  overflow: hidden;
  background:
    linear-gradient(180deg, rgba(42, 47, 54, 0.54), rgba(28, 31, 36, 0.62));
  border-left: 1px solid rgba(167, 227, 178, 0.1);
  display: flex;
  flex-direction: column;
  color: var(--color-ink);
  z-index: 15;
}

.chat-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 0%, rgba(167, 227, 178, 0.018) 100%);
  opacity: 0.2;
  pointer-events: none;
}

.chat-header,
.chat-info-panel,
.chat-setup,
.chat-messages,
.chat-input-area {
  position: relative;
  z-index: 1;
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  font-weight: 700;
  color: var(--color-ink);
  font-size: 0.9rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.chat-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chat-clear-btn,
.chat-gear-btn,
.chat-info-btn {
  background: none;
  border: none;
  color: var(--color-muted);
  cursor: pointer;
  line-height: 1;
}

.chat-clear-btn {
  font-size: 0.75rem;
  padding: 2px 6px;
}

.chat-gear-btn,
.chat-info-btn {
  font-size: 1rem;
  padding: 2px 4px;
}

.chat-clear-btn:hover,
.chat-gear-btn:hover {
  color: var(--color-ink);
}

.chat-info-btn:hover,
.chat-info-btn--active {
  color: var(--color-leaf);
}

.chat-info-panel {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(167, 227, 178, 0.08);
  background: rgba(28, 31, 36, 0.52);
  overflow-y: auto;
  max-height: 280px;
  font-size: 0.75rem;
  color: rgba(237, 242, 235, 0.72);
  line-height: 1.55;
}

.chat-info-heading {
  margin: 0 0 8px;
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--color-ink);
}

.chat-info-section {
  margin: 8px 0 4px;
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--color-moss);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chat-info-list {
  margin: 0;
  padding-left: 14px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.chat-info-list code {
  background: rgba(167, 227, 178, 0.08);
  color: var(--color-leaf);
  padding: 0 3px;
  border-radius: 3px;
  font-size: 0.72rem;
}

.chat-setup {
  padding: 20px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.chat-setup-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-ink);
  margin: 0;
}

.chat-setup-sub {
  font-size: 0.75rem;
  color: var(--color-muted);
  margin: 0;
}

.chat-setup-label {
  font-size: 0.75rem;
  color: rgba(237, 242, 235, 0.72);
  margin-bottom: -4px;
}

.chat-setup-select,
.chat-setup-input {
  padding: 8px 12px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  background: rgba(28, 31, 36, 0.58);
  color: var(--color-ink);
  font-size: 0.85rem;
  outline: none;
  width: 100%;
  box-sizing: border-box;
}

.chat-setup-select:focus,
.chat-setup-input:focus {
  border-color: rgba(167, 227, 178, 0.28);
}

.chat-demo-note {
  font-size: 0.75rem;
  color: var(--color-muted);
  line-height: 1.5;
  padding: 8px 10px;
  background: rgba(47, 125, 79, 0.08);
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
}

.chat-setup-actions {
  display: flex;
  gap: 8px;
}

.chat-btn-primary {
  flex: 1;
  padding: 8px 12px;
  background: rgba(47, 125, 79, 0.18);
  color: var(--color-leaf);
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: background 0.15s, border-color 0.15s;
}

.chat-btn-primary:hover:not(:disabled) {
  background: rgba(47, 125, 79, 0.28);
  border-color: rgba(167, 227, 178, 0.3);
}

.chat-btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.chat-btn-secondary {
  padding: 8px 12px;
  background: none;
  color: var(--color-muted);
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: color 0.15s;
}

.chat-btn-secondary:hover {
  color: var(--color-ink);
}

.chat-btn-danger {
  padding: 8px 12px;
  background: none;
  color: #d48f72;
  border: 1px solid rgba(217, 122, 58, 0.4);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.8rem;
  transition: background 0.15s;
  width: 100%;
}

.chat-btn-danger:hover {
  background: rgba(217, 122, 58, 0.1);
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
}

.chat-empty {
  color: var(--color-muted);
  font-size: 0.8rem;
  padding: 20px 0;
  line-height: 1.6;
}

.chat-suggestions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-top: 10px;
}

.chat-suggestion {
  background: none;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  color: var(--color-leaf);
  font-size: 0.78rem;
  padding: 6px 10px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}

.chat-suggestion:hover:not(:disabled) {
  background: rgba(47, 125, 79, 0.12);
  border-color: rgba(167, 227, 178, 0.24);
}

.chat-suggestion:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.chat-msg {
  margin-bottom: 12px;
}

.chat-msg--user .chat-msg-content {
  background: rgba(47, 125, 79, 0.28);
  border: 1px solid rgba(167, 227, 178, 0.24);
  color: var(--color-leaf);
  border-radius: 12px 12px 6px 12px;
  padding: 8px 12px;
  margin-left: 40px;
  font-size: 0.85rem;
  line-height: 1.5;
}

.chat-msg--user .chat-msg-content :deep(*) {
  color: inherit;
}

.chat-msg--assistant .chat-msg-content {
  background: rgba(28, 31, 36, 0.72);
  border: 1px solid rgba(167, 227, 178, 0.12);
  color: rgba(237, 242, 235, 0.94);
  border-radius: 12px 12px 12px 6px;
  padding: 8px 12px;
  margin-right: 20px;
  font-size: 0.85rem;
  line-height: 1.5;
}

.chat-msg--assistant .chat-msg-content :deep(*) {
  color: inherit;
}

.chat-msg-content :deep(pre) {
  background: rgba(15, 24, 19, 0.52);
  border-radius: 4px;
  padding: 6px 8px;
  margin: 4px 0;
  overflow-x: auto;
  font-size: 0.75rem;
}

.chat-msg-content :deep(code) {
  background: rgba(167, 227, 178, 0.08);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.8rem;
}

.chat-msg-content :deep(pre code) {
  background: none;
  padding: 0;
}

.chat-tool-pills {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 2px 0;
}

.chat-tool-pills--inline {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(167, 227, 178, 0.08);
}

.chat-tool-pill {
  display: inline-block;
  font-size: 0.68rem;
  color: var(--color-moss);
  background: rgba(47, 125, 79, 0.08);
  border: 1px solid rgba(167, 227, 178, 0.1);
  border-radius: 999px;
  padding: 1px 8px;
  font-family: monospace;
}

.chat-input-area {
  display: flex;
  gap: 8px;
  padding: 12px;
  border-top: 1px solid rgba(167, 227, 178, 0.08);
}

.chat-input-area input {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  background: rgba(28, 31, 36, 0.56);
  color: var(--color-ink);
  caret-color: var(--color-leaf);
  font-size: 0.85rem;
  outline: none;
}

.chat-input-area input:focus {
  border-color: rgba(167, 227, 178, 0.28);
}

.chat-input-area input:disabled {
  opacity: 0.5;
}

.chat-input-area button {
  padding: 8px 16px;
  background: rgba(47, 125, 79, 0.18);
  color: var(--color-leaf);
  border: 1px solid rgba(167, 227, 178, 0.18);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: background 0.15s, border-color 0.15s;
}

.chat-input-area button:hover:not(:disabled) {
  background: rgba(47, 125, 79, 0.28);
  border-color: rgba(167, 227, 178, 0.3);
}

.chat-input-area button:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.send-btn-wrapper {
  position: relative;
  display: inline-flex;
}

.send-tooltip {
  display: none;
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  white-space: nowrap;
  background: rgba(28, 31, 36, 0.82);
  color: var(--color-muted);
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 0.75rem;
  pointer-events: none;
  z-index: 100;
}

.send-tooltip::after {
  content: '';
  position: absolute;
  top: 100%;
  right: 12px;
  border: 5px solid transparent;
  border-top-color: rgba(167, 227, 178, 0.12);
}

.send-btn-wrapper:hover .send-tooltip {
  display: block;
}

.chat-loading {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--color-moss);
  font-style: italic;
  font-size: 0.85rem;
}

.chat-loading-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid rgba(167, 227, 178, 0.2);
  border-top-color: var(--color-moss);
  border-radius: 50%;
  flex-shrink: 0;
  animation: chat-spin 0.8s linear infinite;
}

@keyframes chat-spin {
  to {
    transform: rotate(360deg);
  }
}

@media screen and (max-width: 768px) {
  .chat-input-area input,
  .chat-setup-input {
    font-size: 16px;
  }
}
</style>
