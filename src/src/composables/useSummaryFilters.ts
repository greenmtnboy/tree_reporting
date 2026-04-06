import { computed } from 'vue'
import {
  filterAllowedDimensionFilters,
  type CrossFilterSelection,
  type CrossFilterValueMap,
  type DimensionClick,
  useCrossFilterController,
} from '@trilogy-data/trilogy-studio-components/dashboard'

export const SUMMARY_FILTER_FIELDS = [
  'tree_form',
  'species',
  'native_locality_bucket',
  'hardiness_fit_bucket',
  'water_resilience_bucket',
  'sun_exposure_label',
  'lifespan_bucket',
  'growth_rate',
  'wildlife_value',
  'fire_risk',
] as const
export type SummaryFilterField = (typeof SUMMARY_FILTER_FIELDS)[number]

type SummaryFilterMeta = {
  id: string
  field: string
  label: string
  format?: (value: string) => string
}

const SUMMARY_FILTER_META: SummaryFilterMeta[] = [
  { id: 'tree-form', field: 'tree_form', label: 'Form', format: formatSummaryFilterValue },
  { id: 'manual-species', field: 'species', label: 'Species' },
  { id: 'native-locality', field: 'native_locality_bucket', label: 'Nativeness' },
  { id: 'hardiness-fit', field: 'hardiness_fit_bucket', label: 'Hardiness fit' },
  { id: 'water-resilience', field: 'water_resilience_bucket', label: 'Water resilience' },
  { id: 'sun-exposure', field: 'sun_exposure_label', label: 'Sun exposure' },
  { id: 'lifespan-profile', field: 'lifespan_bucket', label: 'Lifespan' },
  { id: 'growth-rate', field: 'growth_rate', label: 'Growth rate', format: formatSummaryFilterValue },
  { id: 'wildlife-value', field: 'wildlife_value', label: 'Wildlife value', format: formatSummaryFilterValue },
  { id: 'fire-risk', field: 'fire_risk', label: 'Fire risk', format: formatSummaryFilterValue },
]

const SOURCE_BY_FIELD: Record<SummaryFilterField, string> = {
  tree_form: 'tree-form',
  species: 'manual-species',
  native_locality_bucket: 'native-locality',
  hardiness_fit_bucket: 'hardiness-fit',
  water_resilience_bucket: 'water-resilience',
  sun_exposure_label: 'sun-exposure',
  lifespan_bucket: 'lifespan-profile',
  growth_rate: 'growth-rate',
  wildlife_value: 'wildlife-value',
  fire_risk: 'fire-risk',
}

const SUMMARY_FILTER_META_BY_FIELD = new Map(
  SUMMARY_FILTER_META.map((meta) => [meta.field, meta] as const),
)

export function filterSummaryCrossFilterFields(filters: CrossFilterValueMap) {
  return filterAllowedDimensionFilters(filters, SUMMARY_FILTER_FIELDS, {
    normalizeLocalFields: true,
  })
}

export function filterSummaryDimensionClick(info: DimensionClick): DimensionClick | null {
  const filters = filterSummaryCrossFilterFields(info.filters)

  if (Object.keys(filters).length === 0) {
    return null
  }

  return {
    ...info,
    filters,
  }
}

const crossFilters = useCrossFilterController({
  validFields: SUMMARY_FILTER_FIELDS,
  normalizeLocalFields: true,
})

function normalizeValues(values: string[]) {
  return Array.from(
    new Set(
      values
        .map((value) => value.trim())
        .filter(Boolean),
    ),
  )
}

export function formatSummaryFilterValue(value: string) {
  return value.replace(/_/g, ' ')
}

function humanizeFieldName(field: string) {
  return field
    .replace(/^local\./, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function labelForField(field: string) {
  return SUMMARY_FILTER_META_BY_FIELD.get(field)?.label ?? humanizeFieldName(field)
}

function formatValueForField(field: string, value: string) {
  return SUMMARY_FILTER_META_BY_FIELD.get(field)?.format?.(value) ?? value
}

function applyValuesForField(
  field: SummaryFilterField,
  values: string[],
  mode: 'replace' | 'append' = 'replace',
) {
  const source = SOURCE_BY_FIELD[field]
  const nextValues = normalizeValues(values)

  if (mode === 'replace') {
    crossFilters.clearSource(source)
  }

  if (nextValues.length === 0) {
    return
  }

  nextValues.forEach((value, index) => {
    crossFilters.applyDimensionClick(
      {
        source,
        filters: { [field]: { op: 'eq' as const, value } },
      },
      mode === 'append' || index > 0 ? 'append' : 'add',
    )
  })
}

function clearFields(fields?: SummaryFilterField[]) {
  if (!fields?.length) {
    crossFilters.clearAll()
    return
  }

  fields.forEach((field) => {
    crossFilters.clearSource(SOURCE_BY_FIELD[field])
  })
}

function selectionsByField() {
  void crossFilters.version.value
  const grouped: Record<string, string[]> = {}

  for (const selection of crossFilters.getSelections()) {
    for (const [field, entry] of Object.entries(selection.filters)) {
      if (entry.op !== 'eq' || typeof entry.value !== 'string') continue
      grouped[field] = normalizeValues([...(grouped[field] ?? []), entry.value])
    }
  }

  return grouped
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
          `${labelForField(field)}: ${values.map((value) => formatValueForField(field, value)).join(', ')}`,
      )

    if (!parts.length) {
      return []
    }

    return [
      {
        key: source,
        source,
        display: parts.join(' | '),
      },
    ]
  })
}

function getPromptState() {
  const active = getActiveFilterSummary()
  if (!active.length) {
    return 'none'
  }
  return active.map((entry) => entry.display).join('; ')
}

export function useSummaryFilters() {
  return {
    crossFilters,
    crossFilterableCharts: SUMMARY_FILTER_META,
    summaryFilterFields: SUMMARY_FILTER_FIELDS,
    summaryFilterSources: SOURCE_BY_FIELD,
    activeSummaryFilters: computed(() => getActiveFilterSummary()),
    summaryFilterPromptState: computed(() => getPromptState()),
    formatSummaryFilterValue,
    labelForField,
    selectionsByField,
    applyValuesForField,
    clearFields,
  }
}

export type { CrossFilterSelection }
