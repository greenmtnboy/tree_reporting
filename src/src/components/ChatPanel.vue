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
          <button
            v-for="(tc, idx) in msg.toolCalls"
            :key="tc.id"
            type="button"
            class="chat-tool-pill"
            :class="{ 'chat-tool-pill--error': tc.isError }"
            data-testid="chat-tool-pill"
            :title="tc.isError ? `${tc.name} failed. Click for details.` : `Click for ${tc.name} details.`"
            @click="openInspector(msg.toolCalls, idx)"
          ><span v-if="tc.isError" class="chat-tool-pill-icon" aria-hidden="true">!</span>{{ tc.name }}</button>
        </div>
        <div v-else class="chat-msg-content">
          <div v-if="msg.isLoading" class="chat-loading">
            <span class="chat-loading-spinner"></span>
            {{ thinkingPhrase }}
          </div>
          <template v-else>
            <MarkdownRenderer v-if="msg.content" :markdown="msg.content" />
            <div v-if="msg.toolCalls?.length" class="chat-tool-pills chat-tool-pills--inline">
              <button
                v-for="(tc, idx) in msg.toolCalls"
                :key="tc.id"
                type="button"
                class="chat-tool-pill"
                :class="{ 'chat-tool-pill--error': tc.isError }"
                data-testid="chat-tool-pill"
                :title="tc.isError ? `${tc.name} failed. Click for details.` : `Click for ${tc.name} details.`"
                @click="openInspector(msg.toolCalls, idx)"
              ><span v-if="tc.isError" class="chat-tool-pill-icon" aria-hidden="true">!</span>{{ tc.name }}</button>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- Tool inspector: the calls behind one assistant turn, each with its
         input and the full result text the model was sent. This is the view
         for a retry loop: the same tool failing the same way, call after call. -->
    <Teleport to="body">
      <div
        v-if="inspector && inspectedCall"
        class="tool-inspector-overlay"
        data-testid="tool-inspector"
        @click.self="closeInspector"
      >
        <div class="tool-inspector" role="dialog" aria-modal="true" aria-label="Tool call details">
          <div class="tool-inspector-header">
            <div class="tool-inspector-tabs">
              <button
                v-for="(tc, idx) in inspector.calls"
                :key="tc.id"
                type="button"
                class="tool-inspector-tab"
                :class="{ active: idx === inspector.index, 'tool-inspector-tab--error': tc.isError }"
                @click="inspector.index = idx"
              ><span v-if="tc.isError" class="chat-tool-pill-icon" aria-hidden="true">!</span>{{ tc.name }}</button>
            </div>
            <button class="tool-inspector-close" aria-label="Close" @click="closeInspector">&#x2715;</button>
          </div>
          <div class="tool-inspector-body">
            <div class="tool-inspector-call-title">
              <code class="tool-inspector-call-name">{{ inspectedCall.name }}</code>
              <span v-if="inspector.calls.length > 1" class="tool-inspector-call-index">
                {{ inspector.index + 1 }} of {{ inspector.calls.length }}
              </span>
              <span class="tool-inspector-status" :class="inspectedCall.isError ? 'error' : 'ok'">
                {{ inspectedCall.isError ? 'failed' : 'ok' }}
              </span>
              <button type="button" class="tool-inspector-copy" @click="copyInspectedCall">
                {{ copiedCall ? 'Copied' : 'Copy JSON' }}
              </button>
            </div>
            <div class="tool-inspector-section">
              <div class="tool-inspector-section-label">Input</div>
              <pre class="tool-inspector-pre">{{ formatInput(inspectedCall.input) }}</pre>
            </div>
            <div class="tool-inspector-section">
              <div class="tool-inspector-section-label">
                {{ inspectedCall.output ? 'Result sent to the model' : 'Result' }}
              </div>
              <pre class="tool-inspector-pre">{{ inspectedCall.output || inspectedCall.result || '(no output recorded)' }}</pre>
            </div>
          </div>
        </div>
      </div>
    </Teleport>

    <div v-if="isConfigured && !showSettings" class="chat-input-area">
      <div class="chat-input-shell">
        <input
          v-model="userInput"
          type="text"
          class="chat-input-field"
          :placeholder="inputPlaceholder"
          @keydown.enter="handleSend"
          :disabled="inputDisabled"
        />
        <span class="send-btn-wrapper">
          <button
            class="chat-send-btn"
            @click="handleSend"
            :disabled="inputDisabled || !userInput.trim()"
            aria-label="Send message"
          >
            <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
              <path d="M10 16V4" />
              <path d="M5 9l5-5 5 5" />
            </svg>
          </button>
          <span v-if="sendTooltip" class="send-tooltip">{{ sendTooltip }}</span>
        </span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
import { MarkdownRenderer } from '@trilogy-data/trilogy-studio-components/dashboard'
import { useChat } from '../composables/useChat'
import { MAP_SUGGESTIONS, SUMMARY_SUGGESTIONS, SPECIES_SUGGESTIONS } from '../composables/chatSuggestions'
import type { ToolCallRecord } from '../types'
import { useSummaryDashboardExecution } from '../composables/useSummaryDashboardExecution'
import { useMapLifecycle } from '../composables/useMapLifecycle'
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

const { messages, isLoading, isConfigured, providerType, setConnection, deleteConnection, sendMessage, clearMessages } = useChat()
const { chatReady: mapReady } = useMapLifecycle()
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

// --- Tool inspector ---
// Clicking a pill opens the turn it belongs to with that call selected. The
// turn's other calls are tabs so a whole loop can be read without reopening.
const inspector = ref<{ calls: ToolCallRecord[]; index: number } | null>(null)
const inspectedCall = computed(() => inspector.value?.calls[inspector.value.index] ?? null)
const copiedCall = ref(false)

function openInspector(calls: ToolCallRecord[] | undefined, index: number) {
  if (!calls?.length) return
  copiedCall.value = false
  inspector.value = { calls, index }
}

function closeInspector() {
  inspector.value = null
}

function onInspectorKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && inspector.value) closeInspector()
}

onMounted(() => window.addEventListener('keydown', onInspectorKeydown))
onUnmounted(() => window.removeEventListener('keydown', onInspectorKeydown))

function formatInput(input: unknown): string {
  if (input === undefined) return '(no input)'
  try {
    return JSON.stringify(input, null, 2)
  } catch {
    return String(input)
  }
}

async function copyInspectedCall() {
  const call = inspectedCall.value
  if (!call) return
  const payload = {
    id: call.id,
    name: call.name,
    isError: call.isError ?? false,
    input: call.input,
    result: call.result,
    output: call.output ?? null,
  }
  try {
    await navigator.clipboard.writeText(JSON.stringify(payload, null, 2))
    copiedCall.value = true
    setTimeout(() => { copiedCall.value = false }, 1500)
  } catch (e) {
    console.warn('[ToolInspector] clipboard write failed', e)
  }
}

const _isMapScreen = computed(() => route.name === 'map')
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
  isSummaryScreen.value || isSpeciesScreen.value ? summaryReady.value : mapReady.value,
)

const inputDisabled = computed(() =>
  isLoading.value || !activeDataReady.value,
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
    linear-gradient(180deg, rgba(var(--surface-raised-rgb), 0.54), rgba(var(--surface-rgb), 0.62));
  border-left: 1px solid rgba(var(--accent-rgb), 0.1);
  display: flex;
  flex-direction: column;
  color: var(--color-ink);
  z-index: 15;
}

.chat-panel::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(180deg, transparent 0%, rgba(var(--accent-rgb), 0.018) 100%);
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
  min-height: 64px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--color-border);
  font-family: var(--font-display);
  font-weight: 400;
  color: var(--color-ink);
  font-size: 1.2rem;
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
  border-bottom: 1px solid rgba(var(--accent-rgb), 0.08);
  background: rgba(var(--surface-rgb), 0.52);
  overflow-y: auto;
  max-height: 280px;
  font-size: 0.75rem;
  color: rgba(var(--ink-rgb), 0.72);
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
  background: rgba(var(--accent-rgb), 0.08);
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
  color: rgba(var(--ink-rgb), 0.72);
  margin-bottom: -4px;
}

.chat-setup-select,
.chat-setup-input {
  padding: 8px 12px;
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  border-radius: 8px;
  background: rgba(var(--surface-rgb), 0.58);
  color: var(--color-ink);
  font-size: 0.85rem;
  outline: none;
  width: 100%;
  box-sizing: border-box;
}

.chat-setup-select:focus,
.chat-setup-input:focus {
  border-color: rgba(var(--accent-rgb), 0.28);
}

.chat-demo-note {
  font-size: 0.75rem;
  color: var(--color-muted);
  line-height: 1.5;
  padding: 8px 10px;
  background: rgba(var(--accent-rgb), 0.08);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  border-radius: 8px;
}

.chat-setup-actions {
  display: flex;
  gap: 8px;
}

.chat-btn-primary {
  flex: 1;
  padding: 8px 12px;
  background: rgba(var(--accent-rgb), 0.18);
  color: var(--color-leaf);
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  border-radius: 8px;
  cursor: pointer;
  font-size: 0.85rem;
  transition: background 0.15s, border-color 0.15s;
}

.chat-btn-primary:hover:not(:disabled) {
  background: rgba(var(--accent-rgb), 0.28);
  border-color: rgba(var(--accent-rgb), 0.3);
}

.chat-btn-primary:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.chat-btn-secondary {
  padding: 8px 12px;
  background: none;
  color: var(--color-muted);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
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
  color: var(--color-autumn);
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
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  border-radius: 8px;
  color: var(--color-leaf);
  font-size: 0.78rem;
  padding: 6px 10px;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s, border-color 0.15s;
}

.chat-suggestion:hover:not(:disabled) {
  background: rgba(var(--accent-rgb), 0.12);
  border-color: rgba(var(--accent-rgb), 0.24);
}

.chat-suggestion:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.chat-msg {
  margin-bottom: 12px;
}

.chat-msg--user .chat-msg-content {
  background: rgba(var(--accent-rgb), 0.28);
  border: 1px solid rgba(var(--accent-rgb), 0.24);
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
  background: rgba(var(--surface-rgb), 0.72);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  color: rgba(var(--ink-rgb), 0.94);
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
  background: rgba(var(--surface-rgb), 0.52);
  border-radius: 4px;
  padding: 6px 8px;
  margin: 4px 0;
  overflow-x: auto;
  font-size: 0.75rem;
}

.chat-msg-content :deep(code) {
  background: rgba(var(--accent-rgb), 0.08);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 0.8rem;
}

.chat-msg-content :deep(pre code) {
  background: none;
  padding: 0;
}

/* The shared MarkdownRenderer styles .md-table with light-theme fallbacks
   (var(--sidebar-bg, #f8f9fa) headers, #e1e5e9 borders). Restyle for our dark surface. */
.chat-msg-content :deep(.md-table-wrapper) {
  overflow-x: auto;
  margin: 6px 0;
}

.chat-msg-content :deep(.md-table) {
  border-collapse: collapse;
  font-size: 0.8rem;
}

.chat-msg-content :deep(.md-table th),
.chat-msg-content :deep(.md-table td) {
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  padding: 4px 8px;
  text-align: left;
}

.chat-msg-content :deep(.md-table th) {
  background-color: rgba(var(--accent-rgb), 0.22);
  color: var(--color-leaf);
  font-weight: 600;
}

.chat-msg-content :deep(.md-table tbody tr:nth-child(even)) {
  background-color: rgba(var(--accent-rgb), 0.04);
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
  border-top: 1px solid rgba(var(--accent-rgb), 0.08);
}

/* A pill is a button: it opens the tool inspector on its call. Failed calls
   are tinted so a retry loop is visible at a glance. */
.chat-tool-pill {
  display: inline-flex;
  align-items: center;
  font-size: 0.68rem;
  color: var(--color-moss);
  background: rgba(var(--accent-rgb), 0.08);
  border: 1px solid rgba(var(--accent-rgb), 0.1);
  border-radius: 999px;
  padding: 1px 8px;
  font-family: monospace;
  line-height: 1.5;
  cursor: pointer;
  appearance: none;
  transition: border-color 0.15s ease, color 0.15s ease;
}

.chat-tool-pill:hover,
.chat-tool-pill:focus-visible {
  border-color: rgba(var(--accent-rgb), 0.4);
  color: var(--color-leaf);
  outline: none;
}

.chat-tool-pill--error {
  color: var(--color-error);
  background: rgba(217, 122, 58, 0.12);
  border-color: rgba(239, 68, 68, 0.4);
}

.chat-tool-pill--error:hover,
.chat-tool-pill--error:focus-visible {
  border-color: rgba(239, 68, 68, 0.7);
  color: var(--color-error);
}

/* The bubble's `:deep(*) { color: inherit }` would otherwise flatten the
   inline pills' tint. */
.chat-msg-content .chat-tool-pill {
  color: var(--color-moss);
}

.chat-msg-content .chat-tool-pill:hover,
.chat-msg-content .chat-tool-pill:focus-visible {
  color: var(--color-leaf);
}

.chat-msg-content .chat-tool-pill--error {
  color: var(--color-error);
}

.chat-tool-pill-icon {
  display: inline-block;
  margin-right: 5px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  font-size: 0.6rem;
  font-weight: 700;
  line-height: 12px;
  text-align: center;
  color: #1c1f24;
  background: #ef4444;
}

/* --- Tool inspector --- */

.tool-inspector-overlay {
  position: fixed;
  inset: 0;
  background: rgba(6, 10, 14, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
  padding: 16px;
}

.tool-inspector {
  width: 100%;
  max-width: 760px;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, rgba(var(--surface-rgb), 0.98), rgba(var(--surface-rgb), 0.98));
  border: 1px solid rgba(var(--accent-rgb), 0.18);
  box-shadow: 0 24px 56px rgba(6, 8, 10, 0.55);
  color: var(--color-ink);
}

.tool-inspector-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 12px 12px 16px;
  border-bottom: 1px solid rgba(var(--accent-rgb), 0.1);
}

.tool-inspector-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  min-width: 0;
}

.tool-inspector-tab {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-family: monospace;
  background: rgba(var(--surface-raised-rgb), 0.9);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  color: var(--color-muted);
  cursor: pointer;
  appearance: none;
}

.tool-inspector-tab:hover {
  color: var(--color-ink);
}

.tool-inspector-tab.active {
  border-color: var(--color-moss);
  color: var(--color-leaf);
  background: rgba(var(--accent-rgb), 0.18);
}

.tool-inspector-tab--error {
  color: var(--color-error);
}

.tool-inspector-tab--error.active {
  border-color: rgba(239, 68, 68, 0.7);
  background: rgba(239, 68, 68, 0.12);
  color: var(--color-error);
}

.tool-inspector-close {
  flex-shrink: 0;
  background: none;
  border: none;
  color: var(--color-muted);
  font-size: 0.9rem;
  cursor: pointer;
  padding: 2px 6px;
}

.tool-inspector-close:hover {
  color: var(--color-ink);
}

.tool-inspector-body {
  overflow-y: auto;
  padding: 12px 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tool-inspector-call-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 0.85rem;
}

.tool-inspector-call-name {
  font-family: monospace;
  font-weight: 600;
  color: var(--color-leaf);
  background: none;
  padding: 0;
}

.tool-inspector-call-index {
  font-family: monospace;
  font-size: 0.7rem;
  color: var(--color-muted);
}

.tool-inspector-status {
  font-family: monospace;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  padding: 1px 6px;
  border-radius: 3px;
}

.tool-inspector-status.ok {
  color: var(--color-leaf);
  background: rgba(var(--accent-rgb), 0.18);
}

.tool-inspector-status.error {
  color: var(--color-error);
  background: rgba(239, 68, 68, 0.15);
}

.tool-inspector-copy {
  margin-left: auto;
  font-size: 0.7rem;
  color: var(--color-moss);
  background: rgba(var(--accent-rgb), 0.08);
  border: 1px solid rgba(var(--accent-rgb), 0.14);
  border-radius: 999px;
  padding: 2px 10px;
  cursor: pointer;
  appearance: none;
}

.tool-inspector-copy:hover {
  color: var(--color-leaf);
  border-color: rgba(var(--accent-rgb), 0.4);
}

.tool-inspector-section-label {
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-muted);
  margin-bottom: 4px;
}

/* Result text can be a full query result: scroll it inside the dialog. */
.tool-inspector-pre {
  margin: 0;
  padding: 10px 12px;
  max-height: 40vh;
  overflow: auto;
  background: rgba(var(--surface-rgb), 0.6);
  border: 1px solid rgba(var(--accent-rgb), 0.1);
  border-radius: 6px;
  font-family: monospace;
  font-size: 0.75rem;
  line-height: 1.5;
  color: rgba(var(--ink-rgb), 0.9);
  white-space: pre-wrap;
  word-break: break-word;
}

@media (max-width: 640px) {
  .tool-inspector-overlay {
    padding: 0;
    align-items: stretch;
  }

  .tool-inspector {
    max-width: none;
    max-height: none;
    border: none;
  }

  .tool-inspector-pre {
    max-height: 45vh;
  }
}

.chat-input-area {
  padding: 12px;
  border-top: 1px solid rgba(var(--accent-rgb), 0.08);
}

.chat-input-shell {
  position: relative;
  display: flex;
  align-items: center;
  width: 100%;
  padding: 6px;
  border: 1px solid rgba(var(--accent-rgb), 0.12);
  border-radius: 999px;
  background: rgba(var(--surface-rgb), 0.72);
  box-shadow: 0 12px 28px rgba(7, 10, 11, 0.16);
}

.chat-input-shell:focus-within {
  border-color: rgba(var(--accent-rgb), 0.28);
  box-shadow: 0 14px 30px rgba(12, 22, 16, 0.22);
}

.chat-input-field {
  flex: 1;
  min-width: 0;
  padding: 10px 56px 10px 14px;
  border: none;
  background: transparent;
  color: var(--color-ink);
  caret-color: var(--color-leaf);
  font-size: 0.85rem;
  outline: none;
}

.chat-input-field::placeholder {
  color: rgba(var(--muted-rgb), 0.72);
}

.chat-input-field:disabled {
  opacity: 0.5;
}

.send-btn-wrapper {
  position: relative;
  display: inline-flex;
}

.chat-send-btn {
  position: absolute;
  top: 50%;
  right: 6px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  padding: 0;
  border: none;
  border-radius: 999px;
  background: rgba(var(--accent-rgb), 0.18);
  color: var(--color-leaf);
  cursor: pointer;
  transform: translateY(-50%);
  transition: background 0.15s, color 0.15s, opacity 0.15s;
}

.chat-send-btn:hover:not(:disabled) {
  background: rgba(var(--accent-rgb), 0.28);
}

.chat-send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.send-tooltip {
  display: none;
  position: absolute;
  bottom: calc(100% + 8px);
  right: 0;
  white-space: nowrap;
  background: rgba(var(--surface-rgb), 0.82);
  color: var(--color-muted);
  border: 1px solid rgba(var(--accent-rgb), 0.12);
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
  border-top-color: rgba(var(--accent-rgb), 0.12);
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
  border: 2px solid rgba(var(--accent-rgb), 0.2);
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
  .chat-input-field,
  .chat-setup-input,
  .chat-setup-select {
    font-size: 16px;
  }
}
</style>
