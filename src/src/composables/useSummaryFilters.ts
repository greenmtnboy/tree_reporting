import { computed } from 'vue'
import {
  type CrossFilterSelection,
  useCrossFilterController,
} from '@trilogy-data/trilogy-studio-components/dashboard'

export const SUMMARY_FILTER_FIELDS = ['tree_category', 'species', 'native_status'] as const
export type SummaryFilterField = (typeof SUMMARY_FILTER_FIELDS)[number]

type SummaryFilterMeta = {
  id: string
  field: SummaryFilterField
  label: string
  format?: (value: string) => string
}

const SUMMARY_FILTER_META: SummaryFilterMeta[] = [
  { id: 'tree-category', field: 'tree_category', label: 'Type', format: formatSummaryFilterValue },
  { id: 'top-species', field: 'species', label: 'Species' },
  { id: 'native-status', field: 'native_status', label: 'Native', format: formatSummaryFilterValue },
]

const SOURCE_BY_FIELD: Record<SummaryFilterField, string> = {
  tree_category: 'tree-category',
  species: 'top-species',
  native_status: 'native-status',
}

const crossFilters = useCrossFilterController({
  validFields: SUMMARY_FILTER_FIELDS,
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

function fieldForSource(source: string): SummaryFilterField | null {
  const match = SUMMARY_FILTER_META.find((meta) => meta.id === source)
  return match?.field ?? null
}

function labelForField(field: SummaryFilterField) {
  return SUMMARY_FILTER_META.find((meta) => meta.field === field)?.label ?? field
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
        filters: { [field]: value },
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
  crossFilters.version.value
  const grouped: Partial<Record<SummaryFilterField, string[]>> = {}

  for (const selection of crossFilters.getSelections()) {
    const field = fieldForSource(selection.source)
    if (!field) continue
    const value = selection.filters[field]
    if (typeof value !== 'string') continue
    grouped[field] = [...(grouped[field] ?? []), value]
  }

  return grouped
}

function getActiveFilterSummary() {
  const grouped = selectionsByField()
  return SUMMARY_FILTER_META.flatMap(({ field, label, format }) => {
    const values = grouped[field]
    if (!values?.length) return []
    const fmt = format ?? ((value: string) => value)
    return [
      {
        field,
        label,
        values,
        display: `${label}: ${values.map(fmt).join(', ')}`,
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
