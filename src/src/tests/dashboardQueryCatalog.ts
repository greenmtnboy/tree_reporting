import { CITY_CONFIG, type CityCode } from '../composables/useMapData'
import {
  getCityBiome,
  getCityEcoregionId,
  getCityRealm,
  getCityUsdaZone,
} from '../composables/dashboardContextSource'
import {
  SUMMARY_CHARTS,
  SUMMARY_DASHBOARD_IMPORTS,
  SUMMARY_KPI_CHARTS,
  TREE_DOT_MAP_QUERY,
  getSummaryBaseFilters,
} from '../composables/summaryDashboardConfig'
import {
  GENUS_OPTIONS_QUERY,
  SPECIES_CHARTS,
  SPECIES_CONTEXT_QUERY,
  SPECIES_DASHBOARD_IMPORTS,
  SPECIES_KPI_CHARTS,
  SPECIES_OPTIONS_QUERY,
  TOP_GENUS_QUERY,
  TOP_SPECIES_QUERY,
  getGenusSqlFilter,
  getSpeciesViewBaseFilters,
} from '../composables/speciesDashboardConfig'
import { SUMMARY_FILTER_FIELDS, useSummaryFilters } from '../composables/useSummaryFilters'
import { SPECIES_FILTER_FIELDS, useSpeciesFilters } from '../composables/useSpeciesFilters'
import { splittingValueFor, type FixtureCityContext } from './dashboardFixtures'

/**
 * What the page is filtered to, stated semantically rather than as SQL. The
 * executor turns this into the expected numbers for a query, so a filter that
 * compiles into SQL but does not actually restrict the rows is caught.
 */
export type QueryState = {
  city: CityCode | null
  genus: string | null
  species: string | null
  crossFilters: Array<{ field: string; value: string }>
}

/**
 * One query a dashboard page issues, with everything the Trilogy resolver needs
 * to compile it: the imports the page declares and the filters it appends.
 * The point of the catalog is that it is derived from the same constants the
 * views render, so a chart cannot be added without the compile test covering it.
 */
export type DashboardQueryCase = {
  /** `{surface}:{chart id}` — stable enough to name a failing chart. */
  id: string
  surface: 'summary' | 'species'
  query: string
  imports: Array<{ name: string; alias: string }>
  filters: string[]
  /** Cross-filter bind values, merged over the dashboard-context parameters. */
  parameters?: Record<string, unknown>
  state: QueryState
}

// Species-page queries compile against a genus/species filter. The binomial and
// genus match the fixtures so the executed results have rows to count; the
// cultivar is deliberately the quoted shape that getSpeciesSqlFilter rewrites
// into LIKE clauses, and matches nothing.
export const SAMPLE_GENUS = 'Platanus'
export const SAMPLE_SPECIES = 'Platanus x hispanica'
export const SAMPLE_CULTIVAR = "Prunus serrulata 'kwanzan'"

export const ALL_CITIES = Object.keys(CITY_CONFIG) as CityCode[]

export function cityContext(city: CityCode | null): FixtureCityContext {
  return {
    ecoregionId: getCityEcoregionId(city),
    usdaZone: getCityUsdaZone(city),
    biome: getCityBiome(city),
    realm: getCityRealm(city),
  }
}

function asImports(imports: Array<{ name: string; alias: string }>) {
  return imports.map((entry) => ({ name: entry.name, alias: entry.alias }))
}

function baseState(city: CityCode | null, overrides: Partial<QueryState> = {}): QueryState {
  return { city, genus: null, species: null, crossFilters: [], ...overrides }
}

/** Every query the summary page issues for `city` (null = the all-cities view). */
export function summaryQueryCases(city: CityCode | null): DashboardQueryCase[] {
  const imports = asImports(SUMMARY_DASHBOARD_IMPORTS)
  const filters = getSummaryBaseFilters(city)
  const state = baseState(city)

  const cases: DashboardQueryCase[] = [
    { id: 'summary:tree-dot-map', surface: 'summary', query: TREE_DOT_MAP_QUERY, imports, filters, state },
  ]

  for (const kpi of SUMMARY_KPI_CHARTS) {
    if (kpi.requiresCitySelection && !city) continue
    cases.push({ id: `summary:${kpi.id}`, surface: 'summary', query: kpi.query, imports, filters, state })
  }

  for (const chart of SUMMARY_CHARTS) {
    if (chart.requiresCitySelection && !city) continue
    cases.push({ id: `summary:${chart.id}`, surface: 'summary', query: chart.query, imports, filters, state })
  }

  return cases
}

/** Every query the species page issues for `city` at the given taxon selection. */
export function speciesQueryCases(
  city: CityCode | null,
  options: { genus?: string | null; species?: string | null } = {},
): DashboardQueryCase[] {
  const genus = options.genus ?? SAMPLE_GENUS
  const species = options.species ?? null
  const imports = asImports(SPECIES_DASHBOARD_IMPORTS)
  const filters = getSpeciesViewBaseFilters(city, genus, species)
  const state = baseState(city, { genus, species })
  const cityFilters = city ? [`city = '${city}'`] : []
  const cityState = baseState(city)
  const genusState = baseState(city, { genus })

  const cases: DashboardQueryCase[] = [
    // The taxonomy selectors run before a genus is chosen, so they see only the
    // city filter (genus selector) or city + genus (species selector).
    { id: 'species:selector-genus-top', surface: 'species', query: TOP_GENUS_QUERY, imports, filters: cityFilters, state: cityState },
    { id: 'species:selector-genus-all', surface: 'species', query: GENUS_OPTIONS_QUERY, imports, filters: cityFilters, state: cityState },
    {
      id: 'species:selector-species-top',
      surface: 'species',
      query: TOP_SPECIES_QUERY,
      imports,
      filters: [...cityFilters, getGenusSqlFilter(genus)],
      state: genusState,
    },
    {
      id: 'species:selector-species-all',
      surface: 'species',
      query: SPECIES_OPTIONS_QUERY,
      imports,
      filters: [...cityFilters, getGenusSqlFilter(genus)],
      state: genusState,
    },
    { id: 'species:context', surface: 'species', query: SPECIES_CONTEXT_QUERY, imports, filters, state },
    { id: 'species:tree-dot-map', surface: 'species', query: TREE_DOT_MAP_QUERY, imports, filters, state },
  ]

  for (const kpi of SPECIES_KPI_CHARTS) {
    if (kpi.requiresCitySelection && !city) continue
    cases.push({ id: `species:${kpi.id}`, surface: 'species', query: kpi.query, imports, filters, state })
  }

  for (const chart of SPECIES_CHARTS) {
    if (chart.requiresCitySelection && !city) continue
    cases.push({ id: `species:${chart.id}`, surface: 'species', query: chart.query, imports, filters, state })
  }

  return cases
}

/** The full set of queries a city's dashboards issue in their default state. */
export function cityQueryCases(city: CityCode | null): DashboardQueryCase[] {
  return [...summaryQueryCases(city), ...speciesQueryCases(city)]
}

type CrossFilterController = ReturnType<typeof useSummaryFilters>['crossFilters']

function withCrossFilters<T>(
  crossFilters: CrossFilterController,
  sourceOf: (field: string) => string,
  selections: Array<{ field: string; value: string }>,
  build: () => T,
): T {
  crossFilters.clearAll()
  for (const selection of selections) {
    crossFilters.applyDimensionClick(
      { source: sourceOf(selection.field), filters: { [selection.field]: { op: 'eq' as const, value: selection.value } } },
      'add',
    )
  }
  try {
    return build()
  } finally {
    crossFilters.clearAll()
  }
}

function casesWithCrossFilter(
  crossFilters: CrossFilterController,
  baseCases: DashboardQueryCase[],
  chartIdOf: (queryCase: DashboardQueryCase) => string,
  sourceOf: (field: string) => string,
  baseFilters: string[],
  selections: Array<{ field: string; value: string }>,
): DashboardQueryCase[] {
  const label = selections.map((selection) => selection.field).join('+')
  return baseCases.map((queryCase) => {
    const chartId = chartIdOf(queryCase)
    const likes = crossFilters.getSqlFilterLikesFor(chartId, baseFilters)
    const parameters = Object.assign(
      {},
      ...likes.map((like) => (like as { parameters?: Record<string, unknown> }).parameters ?? {}),
    ) as Record<string, unknown>
    return {
      ...queryCase,
      id: `${queryCase.id} [cross:${label}]`,
      filters: likes.map((like) => String(like.value)),
      parameters,
      // A chart is not filtered by its own selection — that is what keeps the
      // other slices visible when you click one — so it drops out of the
      // expected rows too.
      state: {
        ...queryCase.state,
        crossFilters: selections.filter((selection) => sourceOf(selection.field) !== chartId),
      },
    }
  })
}

/**
 * The dimension a default run exercises. Nativeness is the one that reaches
 * enrichment through the `unnest(native_ecoregions)` merge, which is where both
 * planner failures found so far have lived — so if only one dimension runs, it
 * should be this one.
 */
export const DEFAULT_CROSS_FILTER_FIELD = 'native_locality_bucket'

export type CrossFilterMode = 'default' | 'all' | 'pairs'

/**
 * Which cross-filter combinations to exercise. `pairs` matters because a user
 * can stack chart clicks and the keyless-join failures need a second filter
 * alongside the first — but it is ten times the compiles, so it is opt-in.
 */
export function crossFilterFieldCombos(mode: CrossFilterMode): string[][] {
  if (mode === 'default') return [[DEFAULT_CROSS_FILTER_FIELD]]

  const fields = SUMMARY_FILTER_FIELDS
  const combos = fields.map((field) => [field])
  if (mode === 'all') return combos
  for (let i = 0; i < fields.length; i += 1) {
    for (let j = i + 1; j < fields.length; j += 1) {
      combos.push([fields[i], fields[j]])
    }
  }
  return combos
}

/**
 * Pair each dimension with a value that splits the fixtures, dropping any
 * dimension that cannot split them in this city's context — see
 * splittingValueFor.
 */
function selectionsFor(fields: readonly string[], city: CityCode | null) {
  const context = cityContext(city)
  return fields.flatMap((field) => {
    const value = splittingValueFor(field, context)
    return value == null ? [] : [{ field, value }]
  })
}

/**
 * The summary page after one or more chart clicks. Every chart is recompiled
 * under the cross-filter state, which is what the base-state cases cannot reach.
 */
export function summaryCrossFilterCases(city: CityCode | null, fields: string[]): DashboardQueryCase[] {
  const usable = fields.filter((field) => (SUMMARY_FILTER_FIELDS as readonly string[]).includes(field))
  if (usable.length === 0) return []

  const { crossFilters, summaryFilterSources } = useSummaryFilters()
  const baseFilters = getSummaryBaseFilters(city)
  const baseCases = summaryQueryCases(city)
  const chartIdOf = (queryCase: DashboardQueryCase) => queryCase.id.replace(/^summary:/, '')
  const sourceOf = (field: string) => summaryFilterSources[field as keyof typeof summaryFilterSources] ?? field
  const selections = selectionsFor(usable, city)
  if (selections.length === 0) return []

  return withCrossFilters(crossFilters, sourceOf, selections, () =>
    casesWithCrossFilter(crossFilters, baseCases, chartIdOf, sourceOf, baseFilters, selections),
  )
}

/** The species page after one or more chart clicks, with a genus and species selected. */
export function speciesCrossFilterCases(city: CityCode | null, fields: string[]): DashboardQueryCase[] {
  const usable = fields.filter((field) => (SPECIES_FILTER_FIELDS as readonly string[]).includes(field))
  if (usable.length === 0) return []

  const { crossFilters, speciesFilterMeta } = useSpeciesFilters()
  const sourceByField = new Map(speciesFilterMeta.map((meta) => [meta.field, meta.id] as const))
  const baseFilters = getSpeciesViewBaseFilters(city, SAMPLE_GENUS, SAMPLE_SPECIES)
  const baseCases = speciesQueryCases(city, { species: SAMPLE_SPECIES })
  const chartIdOf = (queryCase: DashboardQueryCase) => queryCase.id.replace(/^species:/, '')
  const sourceOf = (field: string) => sourceByField.get(field) ?? field
  const selections = selectionsFor(usable, city)
  if (selections.length === 0) return []

  return withCrossFilters(crossFilters, sourceOf, selections, () =>
    casesWithCrossFilter(crossFilters, baseCases, chartIdOf, sourceOf, baseFilters, selections),
  )
}

/** Every query on both pages, under one cross-filter state. */
export function cityCrossFilterCases(city: CityCode | null, fields: string[]): DashboardQueryCase[] {
  return [...summaryCrossFilterCases(city, fields), ...speciesCrossFilterCases(city, fields)]
}

/**
 * The species page with a single species selected, including the cultivar shape
 * whose apostrophe getSpeciesSqlFilter rewrites into LIKE clauses.
 */
export function speciesSelectionCases(city: CityCode | null): DashboardQueryCase[] {
  return [
    ...speciesQueryCases(city, { species: SAMPLE_SPECIES }).map((queryCase) => ({
      ...queryCase,
      id: `${queryCase.id} [species]`,
    })),
    ...speciesQueryCases(city, { genus: 'Prunus', species: SAMPLE_CULTIVAR }).map((queryCase) => ({
      ...queryCase,
      id: `${queryCase.id} [cultivar]`,
    })),
  ]
}
