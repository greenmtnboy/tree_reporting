<template>
  <div class="source-split" :class="{ 'dash-refreshing': loading && loaded }">
    <div v-if="loading && !loaded" class="dash-state">Counting&hellip;</div>
    <div v-else-if="!sources.length" class="dash-state">No source recorded for these trees.</div>
    <template v-else>
      <ul class="source-rows">
        <li v-for="source in sources" :key="source.key" class="source-row" :title="source.title">
          <span class="source-label">{{ source.label }}</span>
          <span class="source-count">{{ source.countText }}</span>
          <span class="source-track" aria-hidden="true">
            <span class="source-bar" :style="{ width: source.width }">
              <span class="source-bar-identified" :style="{ width: source.identifiedWidth }" />
            </span>
          </span>
          <span class="source-note">{{ source.note }}</span>
        </li>
      </ul>
      <div class="source-key" aria-hidden="true">
        <span class="source-key-swatch source-key-swatch--identified" />identified to species
        <span class="source-key-swatch source-key-swatch--unidentified" />species not recorded
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { DashboardImport, EmbeddedDashboardGroup, SqlFilterLike } from '@trilogy-data/trilogy-studio-components/dashboard'
import { rowNumber, rowText, useDashboardRows } from '../composables/useDashboardRows'
import { SOURCE_KIND_LABELS, SOURCE_KIND_ORDER } from '../composables/summaryDashboardConfig'
import { formatCount, formatShare } from '../lib/dashboardFormat'

/**
 * Where a view's tree records come from, one bar per kind of source, each
 * split into the trees identified to species and the ones that are not. The
 * split is the point: a city mapped mostly from aerial imagery has a great
 * many trees and few names, and every species chart on the page is over the
 * named ones.
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
  }>(),
  { imports: () => [], filters: () => [], parameters: () => ({}), priority: 0 },
)

const { rows, loading, loaded } = useDashboardRows(props)

const sources = computed(() => {
  const parsed = rows.value
    .map((row) => ({
      key: rowText(row.source_kind) ?? 'municipal',
      count: rowNumber(row.tree_count) ?? 0,
      identified: rowNumber(row.identified_count) ?? 0,
    }))
    .filter((source) => source.count > 0)
  const rank = (key: string) => {
    const index = (SOURCE_KIND_ORDER as readonly string[]).indexOf(key)
    return index === -1 ? SOURCE_KIND_ORDER.length : index
  }
  parsed.sort((a, b) => rank(a.key) - rank(b.key))
  const peak = Math.max(1, ...parsed.map((source) => source.count))
  return parsed.map((source) => {
    const label = SOURCE_KIND_LABELS[source.key] ?? source.key
    const share = source.identified / source.count
    return {
      key: source.key,
      label,
      countText: formatCount(source.count),
      width: `${Math.max(1.5, (source.count / peak) * 100)}%`,
      identifiedWidth: `${share * 100}%`,
      note: `${formatShare(share)} identified`,
      title: `${label}: ${formatCount(source.count)} trees, ${formatCount(source.identified)} identified to species`,
    }
  })
})
</script>

<style scoped>
.source-split {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1 1 auto;
  min-height: 0;
}

.source-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 9px;
}

.source-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) max-content;
  column-gap: 10px;
  row-gap: 3px;
  align-items: baseline;
}

.source-label {
  font-size: 0.82rem;
  color: var(--color-ink);
}

.source-count {
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
  color: var(--color-ink);
}

.source-track {
  grid-column: 1 / 2;
  height: 11px;
  border-radius: 3px 6px 6px 3px;
  background: rgba(var(--ink-rgb), 0.05);
  align-self: center;
}

.source-bar {
  display: block;
  height: 100%;
  border-radius: 3px 6px 6px 3px;
  overflow: hidden;
  /* The unidentified part: hatched, so it reads as "there, but unnamed". */
  background: repeating-linear-gradient(
    -45deg,
    rgba(var(--ink-rgb), 0.26) 0 2px,
    rgba(var(--ink-rgb), 0.1) 2px 5px
  );
}

.source-bar-identified {
  display: block;
  height: 100%;
  background: var(--color-canopy);
}

.source-note {
  font-size: 0.72rem;
  color: var(--color-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.source-key {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: auto;
  font-size: 0.7rem;
  color: var(--color-muted);
}

.source-key-swatch {
  width: 14px;
  height: 7px;
  border-radius: 2px;
}

.source-key-swatch--identified {
  background: var(--color-canopy);
}

.source-key-swatch--unidentified {
  margin-left: 8px;
  background: repeating-linear-gradient(-45deg, rgba(var(--ink-rgb), 0.26) 0 2px, rgba(var(--ink-rgb), 0.1) 2px 5px);
}
</style>
