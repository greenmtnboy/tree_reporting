import { computed } from 'vue'
import {
  filterAllowedDimensionFilters,
  type CrossFilterSelection,
  type DimensionClick,
  useCrossFilterController,
} from '@trilogy-data/trilogy-studio-components/dashboard'

// Same valid fields as summary — these are the cross-filterable dimensions
// in the species view (species itself is a base filter, not a cross-filter here)
export const SPECIES_FILTER_FIELDS = [
  'tree_form',
  'native_locality_bucket',
  'hardiness_fit_bucket',
  'water_resilience_bucket',
  'sun_exposure_label',
  'lifespan_bucket',
  'growth_rate',
  'wildlife_value',
  'fire_risk',
] as const
export type SpeciesFilterField = (typeof SPECIES_FILTER_FIELDS)[number]

type SpeciesFilterMeta = {
  id: string
  field: string
  label: string
  format?: (value: string) => string
}

export function formatSpeciesFilterValue(value: string) {
  return value.replace(/_/g, ' ')
}

const SPECIES_FILTER_META: SpeciesFilterMeta[] = [
  { id: 'sp-tree-form', field: 'tree_form', label: 'Form', format: formatSpeciesFilterValue },
  { id: 'sp-native-locality', field: 'native_locality_bucket', label: 'Nativeness' },
  { id: 'sp-hardiness-fit', field: 'hardiness_fit_bucket', label: 'Hardiness fit' },
  { id: 'sp-water-resilience', field: 'water_resilience_bucket', label: 'Water resilience' },
  { id: 'sp-sun-exposure', field: 'sun_exposure_label', label: 'Sun exposure' },
  { id: 'sp-lifespan', field: 'lifespan_bucket', label: 'Lifespan' },
  { id: 'sp-growth-rate', field: 'growth_rate', label: 'Growth rate', format: formatSpeciesFilterValue },
  { id: 'sp-wildlife-value', field: 'wildlife_value', label: 'Wildlife value', format: formatSpeciesFilterValue },
  { id: 'sp-fire-risk', field: 'fire_risk', label: 'Fire risk', format: formatSpeciesFilterValue },
]

const SPECIES_FILTER_META_BY_FIELD = new Map(
  SPECIES_FILTER_META.map((meta) => [meta.field, meta] as const),
)

// Separate cross-filter controller so species view state doesn't bleed into summary view
const crossFilters = useCrossFilterController({
  validFields: SPECIES_FILTER_FIELDS,
  normalizeLocalFields: true,
})

export function filterSpeciesDimensionClick(info: DimensionClick): DimensionClick | null {
  const filters = filterAllowedDimensionFilters(info.filters, SPECIES_FILTER_FIELDS, {
    normalizeLocalFields: true,
  })
  if (Object.keys(filters).length === 0) return null
  return { ...info, filters }
}

function normalizeValues(values: string[]) {
  return Array.from(new Set(values.map((v) => v.trim()).filter(Boolean)))
}

function humanizeFieldName(field: string) {
  return field
    .replace(/^local\./, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (l) => l.toUpperCase())
}

function labelForField(field: string) {
  return SPECIES_FILTER_META_BY_FIELD.get(field)?.label ?? humanizeFieldName(field)
}

function formatValueForField(field: string, value: string) {
  return SPECIES_FILTER_META_BY_FIELD.get(field)?.format?.(value) ?? value
}

function getActiveFilterSummary() {
  void crossFilters.version.value
  const grouped = new Map<string, Record<string, string[]>>()

  for (const selection of crossFilters.getSelections()) {
    const sourceFilters = grouped.get(selection.source) ?? {}
    for (const [field, entry] of Object.entries(selection.filters)) {
      if (entry.op !== 'eq' || typeof entry.value !== 'string') continue
      sourceFilters[field] = normalizeValues([...(sourceFilters[field] ?? []), entry.value])
    }
    grouped.set(selection.source, sourceFilters)
  }

  return Array.from(grouped.entries()).flatMap(([source, sourceFilters]) => {
    const parts = Object.entries(sourceFilters)
      .filter(([, values]) => values.length > 0)
      .map(
        ([field, values]) =>
          `${labelForField(field)}: ${values.map((v) => formatValueForField(field, v)).join(', ')}`,
      )
    if (!parts.length) return []
    return [{ key: source, source, display: parts.join(' | ') }]
  })
}

export function useSpeciesFilters() {
  return {
    crossFilters,
    activeSpeciesFilters: computed(() => getActiveFilterSummary()),
    speciesFilterMeta: SPECIES_FILTER_META,
  }
}

export type { CrossFilterSelection }
