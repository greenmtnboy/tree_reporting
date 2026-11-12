<template>
  <article class="species-profile-box" :class="{ 'species-profile--compact': compact }">
    <div class="species-profile">
    <div v-if="photos.length" class="profile-photos" :class="{ 'profile-photos--pair': photos.length > 1 }">
      <figure v-for="photo in photos" :key="photo.kind" class="profile-photo">
        <img :src="photo.url" :alt="`${title}: ${photo.caption.toLowerCase()}`" loading="lazy" />
        <figcaption>
          <span class="profile-photo-kind">{{ photo.caption }}</span>
          <span v-if="photo.attribution" class="profile-photo-credit" :title="photo.attribution">{{ photo.attribution }}</span>
        </figcaption>
      </figure>
    </div>
    <div v-else class="profile-photos profile-photos--none">No photo yet</div>

    <div class="profile-text">
      <header class="profile-heading">
        <h4>{{ title }}</h4>
        <span v-if="scientific && scientific !== title" class="profile-scientific">{{ scientific }}</span>
      </header>
      <p v-if="countLine" class="profile-count">{{ countLine }}</p>
      <p v-if="description" class="profile-description">{{ description }}</p>
      <dl v-if="facts.length" class="profile-facts">
        <div v-for="fact in facts" :key="fact.label" class="profile-fact">
          <dt>{{ fact.label }}</dt>
          <dd>{{ fact.value }}</dd>
        </div>
      </dl>
      <div v-if="$slots.actions" class="profile-actions">
        <slot name="actions" />
      </div>
    </div>
    </div>
  </article>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { rowText } from '../composables/useDashboardRows'
import {
  formatBloomMonths,
  formatEnum,
  formatRange,
  speciesDisplayName,
} from '../lib/dashboardFormat'

/**
 * One species as a field-guide entry: the leaf close-up and the whole-tree
 * view side by side where the enrichment table has both, the names, the
 * description, and what is known about it as a grid of facts rather than a
 * bulleted list. A fact with no value is left out, so a thinly enriched
 * species is a short entry and not a column of dashes.
 *
 * `row` is a row of SPECIES_PROFILE_FIELDS (speciesDashboardConfig.ts).
 */
const props = withDefaults(
  defineProps<{
    row: Record<string, unknown>
    /** e.g. "37,413 trees in Boston · 2nd most common". */
    countLine?: string | null
    compact?: boolean
  }>(),
  { countLine: null, compact: false },
)

const scientific = computed(() => rowText(props.row.species))
const title = computed(() => speciesDisplayName(props.row.species, props.row.common_name))
const description = computed(() => rowText(props.row.description))

const photos = computed(() => {
  const list: Array<{ kind: string; url: string; caption: string; attribution: string | null }> = []
  const leaf = rowText(props.row.photo_url)
  const tree = rowText(props.row.trunk_photo_url)
  if (leaf) list.push({ kind: 'leaf', url: leaf, caption: 'Leaf', attribution: rowText(props.row.photo_attribution) })
  if (tree) list.push({ kind: 'tree', url: tree, caption: 'Whole tree', attribution: rowText(props.row.trunk_photo_attribution) })
  return list
})

function zoneRange(min: unknown, max: unknown): string | null {
  const a = typeof min === 'number' ? min : null
  const b = typeof max === 'number' ? max : null
  if (a == null || b == null) return null
  return a === b ? `Zone ${a}` : `Zones ${a}–${b}`
}

function lifespan(min: unknown, max: unknown): string | null {
  const range = formatRange(min, max, 'years')
  return range
}

function leafHabit(value: unknown): string | null {
  if (value === true) return 'Evergreen'
  if (value === false) return 'Deciduous'
  return null
}

const facts = computed(() => {
  const r = props.row
  const form = r.tree_form === 'default' ? null : formatEnum(r.tree_form)
  const entries: Array<[string, string | null]> = [
    ['Family', rowText(r.family)],
    ['Form', form],
    ['Leaf habit', leafHabit(r.is_evergreen)],
    ['Mature height', formatRange(r.mature_height_min_ft, r.mature_height_max_ft, 'ft')],
    ['Canopy spread', formatRange(r.canopy_spread_min_ft, r.canopy_spread_max_ft, 'ft')],
    ['Growth', formatEnum(r.growth_rate)],
    ['Lifespan', lifespan(r.lifespan_min_years, r.lifespan_max_years)],
    ['Blooms', formatBloomMonths(r.bloom_months)],
    ['Hardiness', zoneRange(r.usda_zone_min, r.usda_zone_max)],
    ['Water needs', formatEnum(r.water_needs)],
    ['Drought tolerance', formatEnum(r.drought_tolerance)],
    ['Wildlife value', formatEnum(r.wildlife_value)],
    ['Fire risk', formatEnum(r.fire_risk)],
    ['In this city', rowText(r.native_locality_bucket)],
  ]
  return entries
    .filter((entry): entry is [string, string] => Boolean(entry[1]))
    .map(([label, value]) => ({ label, value }))
})
</script>

<style scoped>
.species-profile-box {
  min-width: 0;
  min-height: 0;
  container-type: inline-size;
}

.species-profile {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.4fr);
  gap: 18px;
  min-height: 0;
}

.profile-photos {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 10px;
  align-content: start;
  min-width: 0;
}

.profile-photos--pair {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.profile-photos--none {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  border: 1px dashed var(--journal-rule, var(--color-border));
  border-radius: 12px;
  color: var(--color-muted);
  font-size: 0.8rem;
  font-style: italic;
}

.profile-photo {
  margin: 0;
  min-width: 0;
}

.profile-photo img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 5;
  object-fit: cover;
  border-radius: 12px 9px 13px 10px / 10px 13px 9px 12px;
  border: 1px solid var(--journal-rule, var(--color-border));
  background: rgba(var(--ink-rgb), 0.05);
}

.profile-photo figcaption {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-top: 5px;
  min-width: 0;
}

.profile-photo-kind {
  flex: 0 0 auto;
  font-size: 0.64rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.profile-photo-credit {
  min-width: 0;
  font-size: 0.64rem;
  color: rgba(var(--muted-rgb), 0.8);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.profile-text {
  display: flex;
  flex-direction: column;
  gap: 9px;
  min-width: 0;
}

.profile-heading {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.profile-heading h4 {
  margin: 0;
  font-family: var(--font-display);
  font-size: 1.4rem;
  font-weight: 400;
  letter-spacing: -0.015em;
  line-height: 1.2;
}

.profile-scientific {
  font-size: 0.88rem;
  font-style: italic;
  color: rgba(var(--ink-rgb), 0.7);
}

.profile-count {
  margin: 0;
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--color-leaf);
}

.profile-description {
  margin: 0;
  font-size: 0.86rem;
  line-height: 1.6;
  color: rgba(var(--ink-rgb), 0.8);
}

.profile-facts {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
  gap: 8px 14px;
  margin: 2px 0 0;
  padding-top: 10px;
  border-top: 1px solid var(--journal-rule, var(--color-border));
}

.profile-fact {
  min-width: 0;
}

.profile-fact dt {
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.09em;
  text-transform: uppercase;
  color: var(--color-moss);
}

.profile-fact dd {
  margin: 1px 0 0;
  font-size: 0.84rem;
  color: var(--color-ink);
}

.profile-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 2px;
}

.species-profile--compact .profile-description {
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* A narrow card stacks the photos over the text. */
@container (max-width: 520px) {
  .species-profile {
    grid-template-columns: minmax(0, 1fr);
  }

  .profile-photo img {
    aspect-ratio: 4 / 3;
  }
}
</style>
