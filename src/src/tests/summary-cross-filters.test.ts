import { describe, expect, it, vi } from 'vitest'

vi.mock('@trilogy-data/trilogy-studio-components/dashboard', () => {
  function filterAllowedDimensionFilters(
    filters: Record<string, string>,
    validFields: Iterable<string> = [],
    options: { normalizeLocalFields?: boolean } = {},
  ) {
    const valid = new Set(validFields)
    const normalizeLocalFields = options.normalizeLocalFields ?? false

    return Object.entries(filters).reduce(
      (acc, [field, value]) => {
        if (typeof value !== 'string') {
          return acc
        }

        if (valid.size === 0) {
          acc[normalizeLocalFields ? field.replace(/^local\./, '') : field] = value
          return acc
        }

        if (valid.has(field)) {
          acc[field] = value
          return acc
        }

        if (field.startsWith('local.')) {
          const stripped = field.replace(/^local\./, '')
          if (valid.has(stripped)) {
            acc[normalizeLocalFields ? stripped : field] = value
          }
        }

        return acc
      },
      {} as Record<string, string>,
    )
  }

  return {
    filterAllowedDimensionFilters,
    useCrossFilterController: () => ({
      version: { value: 0 },
      applyDimensionClick: () => ({}),
      clearSource: () => {},
      clearAll: () => {},
      hasSelectionFrom: () => false,
      getSelections: () => [],
      getSelectionSources: () => [],
      getSqlFilterInputsFor: () => [],
      getChartSelectionsFor: () => [],
      getFilterExpressionFor: () => '',
      getSqlFiltersFor: (_itemId: string, baseFilters: string[] = []) => baseFilters,
    }),
  }
})

import { filterSummaryDimensionClick } from '../composables/useSummaryFilters'

describe('summary cross-filter field gating', () => {
  it('keeps only global summary fields from chart clicks', () => {
    const result = filterSummaryDimensionClick({
      source: 'dominance-curve',
      filters: {
        species: 'Red Maple',
        rank_by_count: '1',
        tree_count: '200',
        curve_color: '#6BAF92',
      },
      chart: {
        rank_by_count: '1',
        species: 'Red Maple',
      },
      append: false,
    })

    expect(result).toEqual({
      source: 'dominance-curve',
      filters: {
        species: 'Red Maple',
      },
      chart: {
        rank_by_count: '1',
        species: 'Red Maple',
      },
      append: false,
    })
  })

  it('normalizes local-prefixed fields when they map to a shared summary field', () => {
    const result = filterSummaryDimensionClick({
      source: 'tree-form',
      filters: {
        'local.tree_form': 'broadleaf',
        cat_color: '#A7E3B2',
      },
      chart: {
        'local.tree_form': 'broadleaf',
      },
      append: false,
    })

    expect(result?.filters).toEqual({
      tree_form: 'broadleaf',
    })
  })

  it('drops clicks that only contain chart-local fields', () => {
    const result = filterSummaryDimensionClick({
      source: 'plant-year',
      filters: {
        plant_year: '1984',
        planting_window: '40-49 years ago',
        color: '#2F7D4F',
      },
      chart: {
        plant_year: '1984',
      },
      append: false,
    })

    expect(result).toBeNull()
  })
})
