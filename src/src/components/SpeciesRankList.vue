<template>
  <div class="rank-list" :class="{ 'dash-refreshing': loading && entries.length > 0 }">
    <div v-if="loading && !entries.length" class="dash-state">Ranking species&hellip;</div>
    <div v-else-if="!entries.length" class="dash-state">No identified species in this view.</div>
    <template v-else>
      <div class="rank-legend" aria-hidden="true">
        <span class="rank-legend-key rank-legend-key--trees" />share of trees
        <template v-if="hasCanopy">
          <span class="rank-legend-key rank-legend-key--canopy" />share of canopy
        </template>
      </div>
      <ol class="rank-rows">
        <li v-for="entry in entries" :key="entry.species">
          <button
            type="button"
            class="rank-row"
            :class="{ 'is-selected': entry.species === selected }"
            :aria-pressed="entry.species === selected"
            :title="entry.title"
            @click="emit('select', entry.species)"
          >
            <span class="rank-number">{{ entry.rank }}</span>
            <img v-if="entry.photo" class="rank-thumb" :src="entry.photo" alt="" loading="lazy" />
            <span v-else class="rank-thumb rank-thumb--none" aria-hidden="true" />
            <span class="rank-names">
              <span class="rank-name">{{ entry.name }}</span>
              <span class="rank-scientific">{{ entry.species }}</span>
            </span>
            <span class="rank-bars" aria-hidden="true">
              <span class="rank-bar rank-bar--trees" :style="{ width: entry.treeWidth }" />
              <span v-if="entry.canopyWidth" class="rank-bar rank-bar--canopy" :style="{ width: entry.canopyWidth }" />
            </span>
            <span class="rank-figures">
              <span class="rank-share">{{ entry.shareText }}</span>
              <span class="rank-count">{{ entry.countText }}</span>
            </span>
          </button>
        </li>
      </ol>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { rowNumber, rowText, type DashboardRow } from '../composables/useDashboardRows'
import { formatCount, formatShare, speciesDisplayName } from '../lib/dashboardFormat'

/**
 * The species of a view in rank order, each with its share of the trees and,
 * where crowns are known, its share of the canopy drawn under it: a species
 * of small street trees and a species of large park trees can hold the same
 * number of stems and very different amounts of shade. Clicking a row selects
 * it; the page decides what selecting means (show its profile, filter to it).
 *
 * Takes rows rather than a query: the page already holds the species rows for
 * the profile beside this list, and the canopy areas come from another summary.
 */
const props = withDefaults(
  defineProps<{
    /** Rows of species, common_name, photo_url, tree_count, ordered by tree_count. */
    rows: DashboardRow[]
    loading?: boolean
    /** Trees the shares are of (the identified trees in the view). */
    totalTrees?: number | null
    /** species -> crown area in square metres. */
    canopyBySpecies?: Map<string, number> | null
    totalCanopy?: number | null
    selected?: string | null
  }>(),
  { loading: false, totalTrees: null, canopyBySpecies: null, totalCanopy: null, selected: null },
)

const emit = defineEmits<{ select: [species: string] }>()

const hasCanopy = computed(() => Boolean(props.canopyBySpecies?.size && props.totalCanopy))

const entries = computed(() => {
  const rows = props.rows
    .map((row) => ({ row, species: rowText(row.species), count: rowNumber(row.tree_count) ?? 0 }))
    .filter((entry): entry is { row: DashboardRow; species: string; count: number } => Boolean(entry.species))
  const total = props.totalTrees || rows.reduce((sum, entry) => sum + entry.count, 0) || 1
  const shares = rows.map((entry) => {
    const canopy = hasCanopy.value ? (props.canopyBySpecies!.get(entry.species) ?? 0) / props.totalCanopy! : null
    return { ...entry, share: entry.count / total, canopyShare: canopy }
  })
  // One scale for both bars, so a species whose canopy bar outruns its tree
  // bar is visibly carrying more than its share.
  const peak = Math.max(1e-9, ...shares.map((entry) => Math.max(entry.share, entry.canopyShare ?? 0)))
  return shares.map((entry, index) => {
    const name = speciesDisplayName(entry.species, entry.row.common_name)
    const canopyText = entry.canopyShare != null ? `, ${formatShare(entry.canopyShare)} of the canopy` : ''
    return {
      species: entry.species,
      rank: index + 1,
      name,
      photo: rowText(entry.row.photo_url),
      treeWidth: `${Math.max(1, (entry.share / peak) * 100)}%`,
      canopyWidth: entry.canopyShare ? `${Math.max(1, (entry.canopyShare / peak) * 100)}%` : null,
      shareText: formatShare(entry.share),
      countText: formatCount(entry.count),
      title: `${name}: ${formatCount(entry.count)} trees, ${formatShare(entry.share)} of identified trees${canopyText}`,
    }
  })
})
</script>

<style scoped>
.rank-list {
  display: flex;
  flex-direction: column;
  min-height: 0;
  flex: 1 1 auto;
}

.rank-legend {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
  font-size: 0.7rem;
  color: var(--color-muted);
}

.rank-legend-key {
  display: inline-block;
  width: 16px;
  height: 6px;
  border-radius: 2px;
}

.rank-legend-key--trees { background: var(--color-canopy); }
.rank-legend-key--canopy { background: var(--color-soil); margin-left: 10px; }

.rank-rows {
  list-style: none;
  margin: 0;
  padding: 0 4px 0 0;
  overflow-y: auto;
  min-height: 0;
  flex: 1 1 auto;
  scrollbar-width: thin;
}

.rank-row {
  display: grid;
  grid-template-columns: 2.2ch 34px minmax(0, 1.1fr) minmax(48px, 1fr) max-content;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 5px 8px 5px 4px;
  border: 1px solid transparent;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.rank-row:hover {
  background: rgba(var(--accent-rgb), 0.08);
}

.rank-row.is-selected {
  background: var(--accent-soft);
  border-color: rgba(var(--accent-rgb), 0.28);
}

.rank-row:focus-visible {
  outline: 2px solid var(--color-leaf);
  outline-offset: 1px;
}

.rank-number {
  font-family: var(--font-display);
  font-size: 0.86rem;
  color: var(--color-muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.rank-thumb {
  width: 34px;
  height: 34px;
  border-radius: 8px 6px 9px 7px / 7px 9px 6px 8px;
  object-fit: cover;
  border: 1px solid var(--journal-rule, var(--color-border));
  background: rgba(var(--ink-rgb), 0.05);
}

.rank-names {
  display: flex;
  flex-direction: column;
  min-width: 0;
  line-height: 1.25;
}

.rank-name {
  font-size: 0.84rem;
  color: var(--color-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-scientific {
  font-size: 0.72rem;
  font-style: italic;
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rank-bars {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.rank-bar {
  display: block;
  height: 7px;
  border-radius: 2px 4px 4px 2px;
}

.rank-bar--trees { background: var(--color-canopy); }
.rank-bar--canopy { height: 4px; background: var(--color-soil); opacity: 0.85; }

.rank-figures {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  line-height: 1.25;
  font-variant-numeric: tabular-nums;
}

.rank-share {
  font-size: 0.84rem;
  color: var(--color-ink);
}

.rank-count {
  font-size: 0.7rem;
  color: var(--color-muted);
}
</style>
