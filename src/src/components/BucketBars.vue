<template>
  <div class="bucket-bars">
    <div v-if="loading && !loaded" class="dash-state">Counting&hellip;</div>
    <div v-else-if="error" class="dash-state">This breakdown could not be loaded.</div>
    <div v-else-if="!bars.length" class="dash-state">{{ emptyMessage }}</div>
    <template v-else>
      <ul class="bucket-list" :class="{ 'bucket-list--clickable': clickable }">
        <li
          v-for="bar in bars"
          :key="bar.key"
          class="bucket-row"
          :class="{ 'is-selected': selected.includes(bar.key), 'is-dimmed': selected.length > 0 && !selected.includes(bar.key) }"
          :role="clickable ? 'button' : undefined"
          :tabindex="clickable ? 0 : undefined"
          :title="bar.title"
          @click="clickable && emit('select', bar.key)"
          @keydown.enter.prevent="clickable && emit('select', bar.key)"
        >
          <span class="bucket-label">{{ bar.label }}</span>
          <span class="bucket-track" aria-hidden="true">
            <span
              v-if="bar.secondaryWidth != null"
              class="bucket-fill bucket-fill--secondary"
              :style="{ width: bar.secondaryWidth, background: bar.color }"
            />
            <span class="bucket-fill" :style="{ width: bar.width, background: bar.color }" />
          </span>
          <span class="bucket-value">{{ bar.valueText }}</span>
          <span class="bucket-share">{{ bar.shareText }}</span>
        </li>
      </ul>
      <p v-if="note" class="bucket-note">{{ note }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import type { DashboardImport, EmbeddedDashboardGroup, SqlFilterLike } from '@trilogy-data/trilogy-studio-components/dashboard'
import { rowNumber, useDashboardRows } from '../composables/useDashboardRows'
import { formatCount, formatShare } from '../lib/dashboardFormat'

/**
 * A breakdown into a handful of buckets, as labelled bars: three or four
 * categories do not need a chart with axes, and drawn this way four of them
 * fit across a wide screen.
 *
 * The bars are the known buckets only. A bucket named in `unknown` (or a null
 * one) is what the data does not say, so it is left out of the bars and
 * reported as coverage instead: `known / denominator`, where the denominator
 * is the trees the page is counting. The page hides a card whose coverage is
 * too thin to read (`coverage` event) and it comes back by itself when the
 * enrichment fills in.
 */
const props = withDefaults(
  defineProps<{
    itemId: string
    query: string
    connectionId: string
    dashboardGroup: EmbeddedDashboardGroup
    imports?: DashboardImport[]
    filters?: SqlFilterLike[] | string[]
    parameters?: Record<string, unknown>
    priority?: number
    /** Result column holding the bucket. */
    labelField: string
    valueField?: string
    /** A second measure drawn as a paler bar behind the first, on the same scale. */
    secondaryField?: string
    secondaryLabel?: string
    /** Buckets in their natural order; anything else follows, largest first. */
    order?: readonly string[]
    /** Display text per bucket; defaults to the bucket itself. */
    labels?: Record<string, string>
    colors?: Record<string, string>
    /** Buckets that mean "not known". */
    unknown?: readonly string[]
    /** The trees the page counts, for coverage; omit to skip the coverage note. */
    denominator?: number | null
    /** What the denominator is, e.g. "identified trees". */
    denominatorLabel?: string
    selected?: string[]
    clickable?: boolean
    maxRows?: number
    /** Fewer known buckets than this reads as no coverage: one bar is not a breakdown. */
    minBuckets?: number
    emptyMessage?: string
  }>(),
  {
    imports: () => [],
    filters: () => [],
    parameters: () => ({}),
    priority: 0,
    valueField: 'tree_count',
    secondaryField: undefined,
    secondaryLabel: undefined,
    order: () => [],
    labels: () => ({}),
    colors: () => ({}),
    unknown: () => ['Unknown'],
    denominator: undefined,
    denominatorLabel: 'trees',
    selected: () => [],
    clickable: false,
    maxRows: 12,
    minBuckets: 1,
    emptyMessage: 'Nothing recorded for the trees in this view.',
  },
)

const emit = defineEmits<{
  select: [bucket: string]
  /** Share of the denominator the known buckets cover, 0..1; null until loaded. */
  coverage: [value: number | null]
}>()

const { rows, loading, error, loaded } = useDashboardRows(props)

type Bucket = { key: string; value: number; secondary: number | null }

const known = computed<Bucket[]>(() => {
  const merged = new Map<string, Bucket>()
  for (const row of rows.value) {
    const raw = row[props.labelField]
    if (raw == null) continue
    const key = String(raw)
    if (props.unknown.includes(key)) continue
    const value = rowNumber(row[props.valueField]) ?? 0
    if (value <= 0) continue
    const entry = merged.get(key) ?? { key, value: 0, secondary: props.secondaryField ? 0 : null }
    entry.value += value
    if (props.secondaryField) entry.secondary = (entry.secondary ?? 0) + (rowNumber(row[props.secondaryField]) ?? 0)
    merged.set(key, entry)
  }
  const rank = (key: string) => {
    const index = props.order.indexOf(key)
    return index === -1 ? Number.MAX_SAFE_INTEGER : index
  }
  return [...merged.values()].sort((a, b) => rank(a.key) - rank(b.key) || b.value - a.value)
})

const knownTotal = computed(() => known.value.reduce((sum, bucket) => sum + bucket.value, 0))

const coverage = computed(() => {
  if (!loaded.value) return null
  if (known.value.length < props.minBuckets) return 0
  if (props.denominator == null) return knownTotal.value > 0 ? 1 : 0
  if (props.denominator <= 0) return 0
  return Math.min(1, knownTotal.value / props.denominator)
})

watch(coverage, (value) => emit('coverage', value), { immediate: true })

const bars = computed(() => {
  const shown = known.value.slice(0, props.maxRows)
  const peak = Math.max(
    1,
    ...shown.map((bucket) => bucket.value),
    ...shown.map((bucket) => bucket.secondary ?? 0),
  )
  const total = knownTotal.value || 1
  return shown.map((bucket) => {
    const label = props.labels[bucket.key] ?? bucket.key
    const share = bucket.value / total
    return {
      key: bucket.key,
      label,
      color: props.colors[bucket.key] ?? 'var(--color-canopy)',
      width: `${Math.max(0.6, (bucket.value / peak) * 100)}%`,
      secondaryWidth: bucket.secondary == null ? null : `${Math.max(0.6, (bucket.secondary / peak) * 100)}%`,
      valueText: formatCount(bucket.value),
      shareText: formatShare(share),
      title:
        bucket.secondary == null
          ? `${label}: ${formatCount(bucket.value)} (${formatShare(share)})`
          : `${label}: ${formatCount(bucket.value)}; ${props.secondaryLabel ?? props.secondaryField}: ${formatCount(bucket.secondary)}`,
    }
  })
})

const note = computed(() => {
  if (props.denominator == null || coverage.value == null || coverage.value >= 0.995) return null
  return `Known for ${formatShare(coverage.value)} of ${props.denominatorLabel}`
})
</script>

<style scoped>
.bucket-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
  flex: 1 1 auto;
}

.bucket-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  grid-template-columns: minmax(0, max-content) minmax(40px, 1fr) max-content max-content;
  column-gap: 10px;
  row-gap: 7px;
  align-items: center;
}

.bucket-row {
  display: grid;
  grid-template-columns: subgrid;
  grid-column: 1 / -1;
  align-items: center;
  border-radius: 4px;
}

.bucket-label {
  font-size: 0.8rem;
  color: var(--color-ink);
  max-width: 17ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bucket-track {
  position: relative;
  height: 11px;
  border-radius: 3px 6px 6px 3px;
  background: rgba(var(--ink-rgb), 0.06);
  overflow: hidden;
}

.bucket-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 3px 6px 6px 3px;
  transition: width 0.35s ease;
}

.bucket-fill--secondary {
  opacity: 0.32;
}

.bucket-value,
.bucket-share {
  font-size: 0.76rem;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.bucket-value {
  color: var(--color-ink);
}

.bucket-share {
  color: var(--color-muted);
  min-width: 4ch;
}

.bucket-list--clickable .bucket-row > * {
  cursor: pointer;
}

.bucket-list--clickable .bucket-row:hover .bucket-label,
.bucket-row.is-selected .bucket-label {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.bucket-row.is-dimmed > * {
  opacity: 0.45;
}

.bucket-row:focus-visible .bucket-label {
  outline: 2px solid var(--color-leaf);
  outline-offset: 2px;
}

.bucket-note {
  margin: auto 0 0;
  font-size: 0.72rem;
  font-style: italic;
  color: var(--color-muted);
}
</style>
