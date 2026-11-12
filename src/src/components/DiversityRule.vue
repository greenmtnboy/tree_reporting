<template>
  <div class="diversity-rule">
    <ul class="rule-rows">
      <li v-for="row in rows" :key="row.level" class="rule-row" :title="row.title">
        <span class="rule-level">{{ row.level }}</span>
        <span class="rule-name">{{ row.name ?? '–' }}</span>
        <span class="rule-share" :class="{ 'is-over': row.over }">{{ row.shareText }}</span>
        <span class="rule-track" aria-hidden="true">
          <span class="rule-fill" :class="{ 'is-over': row.over }" :style="{ width: row.width }" />
          <span class="rule-limit" :style="{ left: row.limitLeft }" />
        </span>
        <span class="rule-limit-text">limit {{ row.limitText }}</span>
      </li>
    </ul>
    <p class="rule-note">
      The 10-20-30 guideline: no more than 10% of a city’s trees from one species, 20% from one genus and 30% from
      one family, so that one pest or disease cannot take most of the canopy.<template v-if="!family">
      Families are not recorded yet.</template>
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatShare } from '../lib/dashboardFormat'

/**
 * The 10-20-30 rule of thumb for urban forest diversity (Santamour, 1990),
 * applied to the view: the largest species, genus and family, each as a share
 * of the identified trees, against its limit.
 */
type Level = { name: string | null; share: number | null }

// `family` is null while the enrichment table records no families at all.
const props = defineProps<{ species: Level; genus: Level; family: Level | null }>()

// One scale for the three bars: 0 to 45%, so each limit sits where it is.
const SCALE_MAX = 0.45

const rows = computed(() =>
  (
    [
      ['Species', props.species, 0.1],
      ['Genus', props.genus, 0.2],
      ['Family', props.family, 0.3],
    ] as Array<[string, Level | null, number]>
  )
    .filter((entry): entry is [string, Level, number] => entry[1] != null)
    .map(([level, value, limit]) => {
    const share = value.share
    const over = share != null && share > limit
    return {
      level,
      name: value.name,
      over,
      shareText: formatShare(share),
      width: share == null ? '0%' : `${Math.min(100, (share / SCALE_MAX) * 100)}%`,
      limitLeft: `${(limit / SCALE_MAX) * 100}%`,
      limitText: formatShare(limit),
      title:
        share == null
          ? `${level}: not known`
          : `${value.name ?? level} is ${formatShare(share)} of identified trees; the guideline is at most ${formatShare(limit)}`,
    }
  }),
)
</script>

<style scoped>
.diversity-rule {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1 1 auto;
}

.rule-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.rule-row {
  display: grid;
  grid-template-columns: 7ch minmax(0, 1fr) max-content;
  column-gap: 10px;
  row-gap: 3px;
  align-items: baseline;
}

.rule-level {
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.rule-name {
  font-size: 0.84rem;
  font-style: italic;
  color: var(--color-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rule-share {
  font-size: 0.84rem;
  font-variant-numeric: tabular-nums;
  color: var(--color-ink);
}

.rule-share.is-over {
  color: var(--dash-poor);
  font-weight: 600;
}

.rule-track {
  position: relative;
  grid-column: 1 / 3;
  height: 9px;
  border-radius: 3px 5px 5px 3px;
  background: rgba(var(--ink-rgb), 0.06);
  align-self: center;
}

.rule-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 3px 5px 5px 3px;
  background: var(--dash-good);
}

.rule-fill.is-over {
  background: var(--dash-poor);
}

.rule-limit {
  position: absolute;
  top: -3px;
  bottom: -3px;
  width: 0;
  border-left: 1.5px solid var(--color-ink);
}

.rule-limit-text {
  font-size: 0.68rem;
  color: var(--color-muted);
  text-align: right;
}

.rule-note {
  margin: auto 0 0;
  font-size: 0.72rem;
  line-height: 1.5;
  font-style: italic;
  color: var(--color-muted);
}
</style>
