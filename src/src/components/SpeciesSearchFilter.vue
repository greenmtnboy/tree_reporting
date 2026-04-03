<template>
  <div ref="rootRef" class="species-search-filter">
    <div
      class="species-search-filter__control"
      :class="{
        'species-search-filter__control--open': open && !disabled,
        'species-search-filter__control--disabled': disabled,
      }"
    >
      <input
        ref="inputRef"
        class="species-search-filter__input"
        type="text"
        :value="searchTerm"
        :placeholder="resolvedPlaceholder"
        :disabled="disabled"
        spellcheck="false"
        autocomplete="off"
        @focus="handleFocus"
        @input="handleInput"
        @keydown.down.prevent="openDropdown"
        @keydown.enter.prevent="handleEnter"
        @keydown.escape.prevent="handleEscape"
      >
      <button
        class="species-search-filter__toggle"
        type="button"
        :disabled="disabled"
        :aria-expanded="open && !disabled ? 'true' : 'false'"
        @click="toggleDropdown"
      >
        <span class="species-search-filter__chevron" />
      </button>
    </div>

    <div v-if="open && !disabled" class="species-search-filter__dropdown">
      <div v-if="loading && options.length === 0" class="species-search-filter__status">
        {{ loadingMessage }}
      </div>
      <template v-else>
        <button
          v-for="option in visibleOptions"
          :key="option.value"
          class="species-search-filter__option"
          type="button"
          @mousedown.prevent="selectOption(option)"
        >
          <span class="species-search-filter__option-label">{{ option.label }}</span>
          <span v-if="option.count != null" class="species-search-filter__option-count">
            {{ option.count.toLocaleString() }}
          </span>
        </button>
        <div v-if="filteredOptions.length === 0" class="species-search-filter__status">
          {{ emptyMessage }}
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type {
  DashboardExecutionService,
  DashboardImport,
} from '@trilogy-data/trilogy-studio-components/dashboard'

export type SpeciesFilterOption = {
  label: string
  value: string
  count: number | null
}

const props = withDefaults(
  defineProps<{
    modelValue: string | null
    connectionId: string
    queryExecutionService: DashboardExecutionService
    connectionReady?: boolean
    imports?: DashboardImport[]
    baseFilters?: string[]
    topQuery: string
    fullQuery: string
    autoSelectTop?: boolean
    disabled?: boolean
    placeholder?: string
    disabledPlaceholder?: string
    loadingMessage?: string
    emptyMessage?: string
    includeAllOption?: boolean
    allOptionLabel?: string
  }>(),
  {
    connectionReady: true,
    imports: () => [],
    baseFilters: () => [],
    autoSelectTop: true,
    disabled: false,
    placeholder: 'Search taxonomy',
    disabledPlaceholder: 'Select a parent filter first',
    loadingMessage: 'Loading options...',
    emptyMessage: 'No matches found.',
    includeAllOption: false,
    allOptionLabel: 'All',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
  optionsLoaded: [options: SpeciesFilterOption[]]
}>()

const rootRef = ref<HTMLDivElement | null>(null)
const inputRef = ref<HTMLInputElement | null>(null)
const options = ref<SpeciesFilterOption[]>([])
const loading = ref(false)
const open = ref(false)
const searchTerm = ref('')
const manualAllSelected = ref(false)
let loadVersion = 0

const allOption = computed<SpeciesFilterOption | null>(() => (
  props.includeAllOption
    ? { value: '__all__', label: props.allOptionLabel, count: null }
    : null
))

const selectedOption = computed(() => {
  if (props.modelValue == null && props.includeAllOption && manualAllSelected.value) {
    return allOption.value
  }
  return options.value.find((option) => option.value === props.modelValue) ?? null
})

const normalizedQuery = computed(() => {
  const value = searchTerm.value.trim().toLowerCase()
  if (!open.value) return ''
  if (selectedOption.value && searchTerm.value === selectedOption.value.label) {
    return ''
  }
  return value
})

const filteredOptions = computed(() => {
  const availableOptions = allOption.value ? [allOption.value, ...options.value] : options.value
  if (!normalizedQuery.value) return availableOptions
  return availableOptions.filter((option) => {
    const haystack = `${option.label} ${option.value}`.toLowerCase()
    return haystack.includes(normalizedQuery.value)
  })
})

const visibleOptions = computed(() => filteredOptions.value.slice(0, 80))

const resolvedPlaceholder = computed(() => (
  props.disabled ? props.disabledPlaceholder : props.placeholder
))

function syncSearchTermToSelection() {
  if (open.value) return
  searchTerm.value = selectedOption.value?.label ?? ''
}

function parseOptionsFromResult(result: { toJSON: () => unknown }): SpeciesFilterOption[] {
  const payload = result.toJSON() as { data?: Array<Record<string, unknown>> }
  const rows = Array.isArray(payload.data) ? payload.data : []
  return rows
    .map((row) => {
      const value = typeof row.option_value === 'string' ? row.option_value : ''
      const optionLabel = typeof row.option_label === 'string' && row.option_label.trim()
        ? row.option_label
        : value
      const count = typeof row.tree_count === 'number'
        ? row.tree_count
        : typeof row.tree_count === 'string'
          ? Number.parseInt(row.tree_count, 10)
          : null
      return {
        value,
        label: count != null && Number.isFinite(count) ? `${optionLabel} (${count.toLocaleString()})` : optionLabel,
        count: count != null && Number.isFinite(count) ? count : null,
      }
    })
    .filter((option) => option.value.length > 0)
}

function mergeOptions(seed: SpeciesFilterOption[], incoming: SpeciesFilterOption[]) {
  const seen = new Set<string>()
  const merged: SpeciesFilterOption[] = []
  for (const option of [...seed, ...incoming]) {
    if (!option.value || seen.has(option.value)) continue
    seen.add(option.value)
    merged.push(option)
  }
  return merged
}

async function runOptionsQuery(version: number, label: string, query: string, extraFilters: string[] = []) {
  const execution = await props.queryExecutionService.executeQueriesBatch(
    props.connectionId,
    [{
      label,
      query,
      extra_filters: [...props.baseFilters, ...extraFilters],
    }],
    'trilogy',
    props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
  )
  const batch = await execution.resultPromise
  if (version !== loadVersion) return null
  const result = batch.results[0]
  if (!result?.success || !result.results) {
    throw new Error(result?.error || `Failed to load ${label}`)
  }
  return parseOptionsFromResult(result.results)
}

async function loadOptions() {
  const version = ++loadVersion
  if (props.disabled || !props.connectionReady) {
    options.value = []
    manualAllSelected.value = false
    emit('optionsLoaded', [])
    loading.value = false
    open.value = false
    syncSearchTermToSelection()
    return
  }

  loading.value = true
  let seededOptions: SpeciesFilterOption[] = props.modelValue
    ? [{ value: props.modelValue, label: props.modelValue, count: null }]
    : []

  try {
    if (props.autoSelectTop && !props.modelValue && !manualAllSelected.value) {
      const topOptions = await runOptionsQuery(version, 'taxonomy-filter-top-option', props.topQuery)
      if (version !== loadVersion || topOptions == null) return
      seededOptions = topOptions
      options.value = topOptions
      emit('optionsLoaded', topOptions)
      if (topOptions.length > 0) {
        emit('update:modelValue', topOptions[0].value)
      }
    } else if (props.modelValue) {
      const escapedValue = props.modelValue.replace(/'/g, "''")
      const countOptions = await runOptionsQuery(
        version,
        'taxonomy-filter-seed-count',
        props.topQuery,
        [`option_value = '${escapedValue}'`],
      )
      if (version !== loadVersion) return
      if (countOptions && countOptions.length > 0) {
        seededOptions = countOptions
      }
      options.value = seededOptions
      emit('optionsLoaded', seededOptions)
      syncSearchTermToSelection()
    } else {
      options.value = seededOptions
      emit('optionsLoaded', seededOptions)
    }

    const allOptions = await runOptionsQuery(version, 'taxonomy-filter-options', props.fullQuery)
    if (version !== loadVersion || allOptions == null) return
    options.value = mergeOptions(seededOptions, allOptions)
    emit('optionsLoaded', options.value)
  } catch {
    if (version !== loadVersion) return
    options.value = seededOptions
    emit('optionsLoaded', seededOptions)
  } finally {
    if (version === loadVersion) {
      loading.value = false
      syncSearchTermToSelection()
    }
  }
}

function handleFocus() {
  if (props.disabled) return
  open.value = true
  inputRef.value?.select()
}

function handleInput(event: Event) {
  searchTerm.value = (event.target as HTMLInputElement).value
  if (!props.disabled) {
    open.value = true
  }
}

function selectOption(option: SpeciesFilterOption) {
  if (option.value === '__all__') {
    manualAllSelected.value = true
    emit('update:modelValue', null)
  } else {
    manualAllSelected.value = false
    emit('update:modelValue', option.value)
  }
  searchTerm.value = option.label
  open.value = false
}

function openDropdown() {
  if (props.disabled) return
  open.value = true
}

function toggleDropdown() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    inputRef.value?.focus()
  } else {
    syncSearchTermToSelection()
  }
}

function handleEnter() {
  if (!open.value) {
    openDropdown()
    return
  }
  const firstOption = filteredOptions.value[0]
  if (firstOption) {
    selectOption(firstOption)
  }
}

function handleEscape() {
  open.value = false
  syncSearchTermToSelection()
}

function handleDocumentPointerDown(event: PointerEvent) {
  const root = rootRef.value
  if (!root) return
  if (event.target instanceof Node && root.contains(event.target)) return
  open.value = false
  syncSearchTermToSelection()
}

watch(
  () => props.modelValue,
  () => {
    if (props.modelValue != null) {
      manualAllSelected.value = false
    }
    syncSearchTermToSelection()
  },
)

watch(
  () => [
    JSON.stringify(props.baseFilters),
    props.connectionReady,
    props.disabled,
    props.topQuery,
    props.fullQuery,
    props.modelValue,
  ],
  () => {
    void loadOptions()
  },
  { immediate: true },
)

watch(
  () => JSON.stringify(props.baseFilters),
  () => {
    manualAllSelected.value = false
  },
)

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  syncSearchTermToSelection()
})

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})
</script>

<style scoped>
.species-search-filter {
  position: relative;
  min-width: 0;
}

.species-search-filter__control {
  display: flex;
  align-items: center;
  min-height: 48px;
  border: 1px solid rgba(167, 227, 178, 0.14);
  border-radius: 12px;
  background: rgba(10, 14, 18, 0.58);
  transition: border-color 0.2s ease, box-shadow 0.2s ease, background 0.2s ease;
}

.species-search-filter__control--open {
  border-color: rgba(107, 175, 146, 0.45);
  box-shadow: 0 0 0 1px rgba(107, 175, 146, 0.18), 0 10px 24px rgba(4, 7, 8, 0.18);
  background: rgba(15, 20, 25, 0.74);
}

.species-search-filter__control--disabled {
  opacity: 0.55;
  background: rgba(28, 31, 36, 0.36);
}

.species-search-filter__input {
  width: 100%;
  min-width: 0;
  border: none;
  background: transparent;
  color: var(--color-foam);
  font-size: 0.92rem;
  padding: 12px 14px;
  outline: none;
}

.species-search-filter__input::placeholder {
  color: rgba(154, 166, 154, 0.76);
}

.species-search-filter__toggle {
  flex: 0 0 auto;
  width: 42px;
  height: 42px;
  border: none;
  background: transparent;
  color: rgba(154, 166, 154, 0.82);
  cursor: pointer;
}

.species-search-filter__toggle:disabled {
  cursor: default;
}

.species-search-filter__chevron {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-right: 1.5px solid currentColor;
  border-bottom: 1.5px solid currentColor;
  transform: rotate(45deg) translateY(-1px);
}

.species-search-filter__dropdown {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  right: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 320px;
  padding: 8px;
  overflow-y: auto;
  border: 1px solid rgba(167, 227, 178, 0.12);
  border-radius: 12px;
  background: rgba(14, 18, 22, 0.98);
  box-shadow: 0 18px 40px rgba(4, 7, 8, 0.35);
}

.species-search-filter__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  width: 100%;
  padding: 9px 10px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: rgba(237, 242, 235, 0.88);
  text-align: left;
  cursor: pointer;
}

.species-search-filter__option:hover {
  background: rgba(47, 125, 79, 0.16);
}

.species-search-filter__option:focus-visible {
  outline: 1px solid rgba(107, 175, 146, 0.45);
  background: rgba(47, 125, 79, 0.16);
}

.species-search-filter__option-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.species-search-filter__option-count {
  flex: 0 0 auto;
  color: rgba(154, 166, 154, 0.76);
  font-size: 0.76rem;
}

.species-search-filter__status {
  padding: 10px 12px;
  color: rgba(154, 166, 154, 0.78);
  font-size: 0.82rem;
}
</style>
