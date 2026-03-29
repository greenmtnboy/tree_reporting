<template>
  <div class="summary-markdown-card">
    <div v-if="loading" class="summary-markdown-card__state">Loading spotlight...</div>
    <div v-else-if="error" class="summary-markdown-card__state summary-markdown-card__state--error">
      {{ error }}
    </div>
    <div v-else-if="markdown" class="summary-markdown-card__content">
      <MarkdownRenderer :markdown="markdown" />
    </div>
    <div v-else class="summary-markdown-card__state">No matching tree data in the current filter set.</div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MarkdownRenderer } from '@trilogy-data/trilogy-studio-components/dashboard'
import type { DashboardImport, DashboardExecutionService } from '@trilogy-data/trilogy-studio-components/dashboard'

const props = withDefaults(
  defineProps<{
    connectionId: string
    queryExecutionService: DashboardExecutionService
    imports?: DashboardImport[]
    query: string
    filters?: string[]
    itemId: string
  }>(),
  {
    imports: () => [],
    filters: () => [],
  },
)

const loading = ref(false)
const error = ref<string | null>(null)
const row = ref<Record<string, unknown> | null>(null)

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function formatTitleCase(value: unknown) {
  if (typeof value !== 'string' || !value.trim()) return null
  return value.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function formatRange(min: unknown, max: unknown, unit: string) {
  const minValue = typeof min === 'number' ? min : null
  const maxValue = typeof max === 'number' ? max : null
  if (minValue == null && maxValue == null) return null
  if (minValue != null && maxValue != null) {
    return minValue === maxValue ? `${minValue} ${unit}` : `${minValue}-${maxValue} ${unit}`
  }
  return `${minValue ?? maxValue} ${unit}`
}

function formatBloomMonths(value: unknown) {
  if (!Array.isArray(value) || value.length === 0) return null
  const months = Array.from(
    new Set(
      value
        .filter((month): month is number => typeof month === 'number' && month >= 1 && month <= 12)
        .sort((a, b) => a - b),
    ),
  )
  if (!months.length) return null
  if (months.length === 12) return 'Year-round'
  const contiguous = months.every((month, index) => index === 0 || month === months[index - 1] + 1)
  if (contiguous) {
    const start = MONTH_LABELS[months[0] - 1]
    const end = MONTH_LABELS[months[months.length - 1] - 1]
    return start === end ? start : `${start}-${end}`
  }
  return months.map((month) => MONTH_LABELS[month - 1]).join(', ')
}

const markdown = computed(() => {
  if (!row.value) return ''

  const species = typeof row.value.species === 'string' ? row.value.species : 'Unknown species'
  const commonName = typeof row.value.common_name === 'string' && row.value.common_name.trim()
    ? row.value.common_name
    : null
  const count = typeof row.value.tree_count === 'number' ? row.value.tree_count.toLocaleString() : null
  const description = typeof row.value.description === 'string' && row.value.description.trim()
    ? row.value.description.trim()
    : null

  const facts = [
    ['Form', formatTitleCase(row.value.tree_form)],
    ['Mature height', formatRange(row.value.mature_height_min_ft, row.value.mature_height_max_ft, 'ft')],
    ['Canopy spread', formatRange(row.value.canopy_spread_min_ft, row.value.canopy_spread_max_ft, 'ft')],
    ['Growth rate', formatTitleCase(row.value.growth_rate)],
    ['Bloom period', formatBloomMonths(row.value.bloom_months)],
    ['Wildlife value', formatTitleCase(row.value.wildlife_value)],
    ['Drought tolerance', formatTitleCase(row.value.drought_tolerance)],
    ['Water needs', formatTitleCase(row.value.water_needs)],
  ].filter(([, value]) => Boolean(value))

  const introName = commonName ? `**${commonName}**` : `**${species}**`
  const introSpecies = commonName ? ` (*${species}*)` : ''
  const introCount = count ? `, with **${count}** trees in the current view.` : '.'

  return [
    `${introName}${introSpecies} is the most common tree in the current filter set${introCount}`,
    description ?? '',
    ...facts.map(([label, value]) => `- **${label}:** ${value}`),
  ]
    .filter(Boolean)
    .join('\n\n')
})

async function load() {
  if (!props.query.trim()) {
    row.value = null
    error.value = null
    return
  }

  loading.value = true
  error.value = null
  try {
    const { resultPromise } = await props.queryExecutionService.executeQueriesBatch(
      props.connectionId,
      [{
        label: props.itemId,
        query: props.query,
        extra_filters: props.filters,
      }],
      'trilogy',
      props.imports.map((imp) => ({ name: imp.name, alias: imp.alias })),
    )
    const batch = await resultPromise
    const result = batch.results[0]
    if (!result?.success || !result.results) {
      throw new Error(result?.error || 'Failed to load spotlight card.')
    }
    const payload = result.results.toJSON() as { data?: Array<Record<string, unknown>> }
    row.value = Array.isArray(payload.data) ? (payload.data[0] ?? null) : null
  } catch (err) {
    row.value = null
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.query, JSON.stringify(props.filters), JSON.stringify(props.imports), props.connectionId],
  () => {
    void load()
  },
  { immediate: true },
)
</script>

<style scoped>
.summary-markdown-card {
  flex: 1 1 auto;
  min-height: 0;
  height: 100%;
  overflow: auto;
}

.summary-markdown-card__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: rgba(154, 166, 154, 0.82);
  font-size: 0.84rem;
}

.summary-markdown-card__state--error {
  color: #d48f72;
}

.summary-markdown-card__content {
  height: 100%;
  color: rgba(237, 242, 235, 0.9);
}

.summary-markdown-card__content :deep(.markdown-root),
.summary-markdown-card__content :deep(.markdown-content),
.summary-markdown-card__content :deep(.markdown-body) {
  color: rgba(237, 242, 235, 0.9);
  font-size: 0.92rem;
  line-height: 1.6;
}

.summary-markdown-card__content :deep(*) {
  color: inherit;
}

.summary-markdown-card__content :deep(p),
.summary-markdown-card__content :deep(li) {
  color: rgba(237, 242, 235, 0.82);
}

.summary-markdown-card__content :deep(strong) {
  color: var(--color-ink);
  font-weight: 700;
}

.summary-markdown-card__content :deep(em) {
  color: rgba(237, 242, 235, 0.88);
}

.summary-markdown-card__content :deep(p:first-child) {
  margin-top: 0;
  color: rgba(237, 242, 235, 0.94);
  font-size: 1rem;
  line-height: 1.55;
}

.summary-markdown-card__content :deep(ul) {
  margin: 0.8rem 0 0;
  padding-left: 1.1rem;
}

.summary-markdown-card__content :deep(li) {
  margin-bottom: 0.35rem;
}

.summary-markdown-card__content :deep(li::marker) {
  color: rgba(167, 227, 178, 0.68);
}

.summary-markdown-card__content :deep(a) {
  color: var(--color-leaf);
}
</style>
