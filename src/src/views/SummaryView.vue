<template>
  <div class="summary-page">
    <div class="summary-header">
      <div class="summary-title-row">
        <h1>Tree Analytics</h1>
        <div class="city-badge">{{ cityName }}</div>
      </div>
      <div v-if="activeFilters.length" class="active-filters">
        <span class="filter-chip" v-for="f in activeFilters" :key="f.key">
          <span class="filter-chip-label">{{ f.label }}</span>
          <button class="filter-chip-clear" @click="clearFilter(f.key)" title="Remove filter">×</button>
        </span>
        <button class="clear-all-btn" @click="clearAllFilters">Clear all</button>
      </div>
    </div>

    <div v-if="!dbReady" class="loading-state">
      <div class="loading-spinner" />
      <span>Loading tree data…</span>
    </div>

    <template v-else>
      <!-- KPI Row -->
      <div class="kpi-row">
        <StatCard label="Total Trees" :value="fmtBig(kpis.total)" />
        <StatCard label="Unique Species" :value="fmtBig(kpis.speciesCount)" />
        <StatCard
          label="Median Trunk Diameter"
          :value="kpis.medianDbh != null ? `${kpis.medianDbh.toFixed(1)}&quot;` : '—'"
          sub="at breast height"
        />
        <StatCard
          label="Evergreen Trees"
          :value="kpis.evergreenPct != null ? `${kpis.evergreenPct.toFixed(0)}%` : '—'"
          :sub="kpis.evergreenCount ? `${fmtBig(kpis.evergreenCount)} trees` : undefined"
        />
      </div>

      <!-- Row 2: Category donut + Top species bar -->
      <div class="chart-row">
        <div class="chart-card chart-card--narrow">
          <div class="chart-card-header">
            <h2>Tree Types</h2>
          </div>
          <DonutChart
            :data="categoryData"
            :selected="selectedCategory"
            @select="toggleCategory"
          />
        </div>

        <div class="chart-card chart-card--wide">
          <div class="chart-card-header">
            <h2>
              Top Species
              <span v-if="selectedCategory" class="header-filter-hint">
                · filtered to <em>{{ selectedCategory }}</em>
              </span>
            </h2>
          </div>
          <HBarChart
            :data="speciesData"
            :selected="selectedSpecies"
            @select="toggleSpecies"
          />
        </div>
      </div>

      <!-- Row 3: Trunk diameter distribution + Planted by decade -->
      <div class="chart-row">
        <div class="chart-card">
          <div class="chart-card-header">
            <h2>Trunk Diameter Distribution</h2>
            <span class="chart-sub">inches at breast height, 5&Prime; buckets</span>
          </div>
          <VBarChart :data="dbhData" />
        </div>

        <div class="chart-card" v-if="decadeData.length > 0">
          <div class="chart-card-header">
            <h2>Trees Planted by Decade</h2>
          </div>
          <VBarChart :data="decadeData" />
        </div>

        <div class="chart-card" v-else>
          <div class="chart-card-header">
            <h2>Native Status</h2>
          </div>
          <HBarChart :data="nativeData" :selected="selectedNative" @select="toggleNative" />
        </div>
      </div>

      <!-- Row 4: Native + Ecological -->
      <div class="chart-row">
        <div class="chart-card" v-if="decadeData.length > 0">
          <div class="chart-card-header">
            <h2>Native Status</h2>
          </div>
          <HBarChart :data="nativeData" :selected="selectedNative" @select="toggleNative" />
        </div>

        <div class="chart-card">
          <div class="chart-card-header">
            <h2>Mature Height Profile</h2>
            <span class="chart-sub">enriched species only</span>
          </div>
          <VBarChart :data="heightData" />
        </div>

        <div class="chart-card">
          <div class="chart-card-header">
            <h2>Drought Tolerance</h2>
            <span class="chart-sub">enriched species only</span>
          </div>
          <HBarChart :data="droughtData" />
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import StatCard from '../components/StatCard.vue'
import HBarChart from '../components/HBarChart.vue'
import type { HBarItem } from '../components/HBarChart.vue'
import DonutChart from '../components/DonutChart.vue'
import type { DonutItem } from '../components/DonutChart.vue'
import VBarChart from '../components/VBarChart.vue'
import type { VBarItem } from '../components/VBarChart.vue'
import { useDuckDB } from '../composables/useDuckDB'
import { useMapData } from '../composables/useMapData'
import cityConfig from '../cityConfig.json'

const { ready: dbReady, query } = useDuckDB()
const { selectedCity } = useMapData()

// ── Cross-filter state ────────────────────────────────────────────────────────
const selectedCategory = ref<string | null>(null)
const selectedSpecies = ref<string | null>(null)
const selectedNative = ref<string | null>(null)

function toggleCategory(label: string) {
  selectedCategory.value = selectedCategory.value === label ? null : label
  selectedSpecies.value = null
}
function toggleSpecies(label: string) {
  selectedSpecies.value = selectedSpecies.value === label ? null : label
}
function toggleNative(label: string) {
  selectedNative.value = selectedNative.value === label ? null : label
}
function clearFilter(key: string) {
  if (key === 'category') selectedCategory.value = null
  if (key === 'species') selectedSpecies.value = null
  if (key === 'native') selectedNative.value = null
}
function clearAllFilters() {
  selectedCategory.value = null
  selectedSpecies.value = null
  selectedNative.value = null
}

const activeFilters = computed(() => {
  const filters: { key: string; label: string }[] = []
  if (selectedCategory.value) filters.push({ key: 'category', label: `Type: ${selectedCategory.value}` })
  if (selectedSpecies.value) filters.push({ key: 'species', label: `Species: ${selectedSpecies.value}` })
  if (selectedNative.value) filters.push({ key: 'native', label: `Native: ${selectedNative.value}` })
  return filters
})

// ── Category colors (mirrors worker + useTreeCategories) ──────────────────────
const CATEGORY_COLORS: Record<string, string> = {
  broadleaf: '#4CAF50',
  spreading: '#8BC34A',
  coniferous: '#2E7D32',
  columnar: '#43A047',
  ornamental: '#E91E63',
  palm: '#e6a835',
  default: '#66BB6A',
}

// ── Display name ──────────────────────────────────────────────────────────────
const cityName = computed(() => {
  const cfg = (cityConfig as Record<string, { name: string }>)[selectedCity.value]
  return cfg?.name ?? selectedCity.value
})

// ── KPIs ──────────────────────────────────────────────────────────────────────
const kpis = ref({
  total: 0,
  speciesCount: 0,
  medianDbh: null as number | null,
  evergreenCount: 0,
  evergreenPct: null as number | null,
})

async function loadKpis() {
  // Build WHERE snippet for active filters
  const where = buildFilterWhere()

  const res = await query(`
    SELECT
      COUNT(*) AS total,
      COUNT(DISTINCT t.species) AS species_count,
      MEDIAN(t.diameter_at_breast_height) AS median_dbh,
      SUM(CASE WHEN se.is_evergreen = true THEN 1 ELSE 0 END) AS evergreen_count
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    ${where}
  `)
  const row = res.rows[0]
  if (!row) return
  const total = Number(row.total ?? 0)
  const evergreen = Number(row.evergreen_count ?? 0)
  kpis.value = {
    total,
    speciesCount: Number(row.species_count ?? 0),
    medianDbh: row.median_dbh != null ? Number(row.median_dbh) : null,
    evergreenCount: evergreen,
    evergreenPct: total > 0 ? (evergreen / total) * 100 : null,
  }
}

// ── Category (donut) ──────────────────────────────────────────────────────────
const categoryData = ref<DonutItem[]>([])

async function loadCategories() {
  const speciesWhere = selectedSpecies.value
    ? `AND t.species = '${selectedSpecies.value.replace(/'/g, "''")}'`
    : ''
  const nativeWhere = selectedNative.value
    ? `AND COALESCE(se.native_status, 'Unknown') = '${selectedNative.value.replace(/'/g, "''")}'`
    : ''

  const res = await query(`
    SELECT
      COALESCE(se.tree_category, 'default') AS category,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE 1=1 ${speciesWhere} ${nativeWhere}
    GROUP BY category
    ORDER BY cnt DESC
  `)
  categoryData.value = res.rows.map((r) => ({
    label: String(r.category ?? 'default'),
    value: Number(r.cnt ?? 0),
    color: CATEGORY_COLORS[String(r.category ?? 'default')] ?? '#66BB6A',
  }))
}

// ── Top species (horizontal bar) ──────────────────────────────────────────────
const speciesData = ref<HBarItem[]>([])

async function loadSpecies() {
  const catWhere = selectedCategory.value
    ? `AND COALESCE(se.tree_category, 'default') = '${selectedCategory.value.replace(/'/g, "''")}'`
    : ''
  const nativeWhere = selectedNative.value
    ? `AND COALESCE(se.native_status, 'Unknown') = '${selectedNative.value.replace(/'/g, "''")}'`
    : ''

  const res = await query(`
    SELECT
      t.species,
      COALESCE(se.tree_category, 'default') AS category,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE t.species IS NOT NULL AND t.species != '' ${catWhere} ${nativeWhere}
    GROUP BY t.species, category
    ORDER BY cnt DESC
    LIMIT 15
  `)
  speciesData.value = res.rows.map((r) => ({
    label: String(r.species ?? ''),
    value: Number(r.cnt ?? 0),
    color: CATEGORY_COLORS[String(r.category ?? 'default')] ?? '#66BB6A',
  }))
}

// ── DBH distribution (vertical bars / histogram) ─────────────────────────────
const dbhData = ref<VBarItem[]>([])

async function loadDbh() {
  const where = buildFilterWhere()
  const res = await query(`
    SELECT
      CAST(FLOOR(t.diameter_at_breast_height / 5) * 5 AS INTEGER) AS bucket,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE t.diameter_at_breast_height BETWEEN 0 AND 79
    ${where.replace('WHERE 1=1', 'AND 1=1')}
    GROUP BY bucket
    ORDER BY bucket
  `)
  dbhData.value = res.rows.map((r) => ({
    label: `${r.bucket}–${Number(r.bucket) + 5}″`,
    value: Number(r.cnt ?? 0),
    color: '#4fc3f7',
  }))
}

// ── Trees planted by decade ───────────────────────────────────────────────────
const decadeData = ref<VBarItem[]>([])

async function loadDecades() {
  const where = buildFilterWhere()
  try {
    const res = await query(`
      SELECT
        CAST(FLOOR(YEAR(t.plant_date) / 10) * 10 AS INTEGER) AS decade,
        COUNT(*) AS cnt
      FROM trees t
      LEFT JOIN species_enrichment se ON t.species = se.species
      WHERE t.plant_date IS NOT NULL AND YEAR(t.plant_date) BETWEEN 1900 AND 2030
      ${where.replace('WHERE 1=1', 'AND 1=1')}
      GROUP BY decade
      ORDER BY decade
    `)
    decadeData.value = res.rows.map((r) => ({
      label: `${r.decade}s`,
      value: Number(r.cnt ?? 0),
      color: '#81c784',
    }))
  } catch {
    decadeData.value = []
  }
}

// ── Native status ─────────────────────────────────────────────────────────────
const nativeData = ref<HBarItem[]>([])
const NATIVE_COLORS: Record<string, string> = {
  Native: '#4CAF50',
  'Non-native': '#ef5350',
  Invasive: '#FF6D00',
  Naturalized: '#FFA726',
  Unknown: '#555577',
}

async function loadNative() {
  const catWhere = selectedCategory.value
    ? `AND COALESCE(se.tree_category, 'default') = '${selectedCategory.value.replace(/'/g, "''")}'`
    : ''
  const speciesWhere = selectedSpecies.value
    ? `AND t.species = '${selectedSpecies.value.replace(/'/g, "''")}'`
    : ''

  const res = await query(`
    SELECT
      COALESCE(se.native_status, 'Unknown') AS native_status,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE 1=1 ${catWhere} ${speciesWhere}
    GROUP BY native_status
    ORDER BY cnt DESC
  `)
  nativeData.value = res.rows.map((r) => ({
    label: String(r.native_status ?? 'Unknown'),
    value: Number(r.cnt ?? 0),
    color: NATIVE_COLORS[String(r.native_status)] ?? '#7a7a9e',
  }))
}

// ── Mature height profile ─────────────────────────────────────────────────────
const HEIGHT_BUCKETS = [
  { label: '<20ft', min: 0, max: 20, color: '#AED581' },
  { label: '20–40ft', min: 20, max: 40, color: '#66BB6A' },
  { label: '40–60ft', min: 40, max: 60, color: '#43A047' },
  { label: '60–80ft', min: 60, max: 80, color: '#2E7D32' },
  { label: '80ft+', min: 80, max: 9999, color: '#1B5E20' },
]
const heightData = ref<VBarItem[]>([])

async function loadHeight() {
  const where = buildFilterWhere()
  const res = await query(`
    SELECT
      se.mature_height_ft,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE se.mature_height_ft IS NOT NULL
    ${where.replace('WHERE 1=1', 'AND 1=1')}
    GROUP BY se.mature_height_ft
  `)
  const buckets = HEIGHT_BUCKETS.map((b) => ({ ...b, count: 0 }))
  for (const row of res.rows) {
    const h = Number(row.mature_height_ft ?? 0)
    const cnt = Number(row.cnt ?? 0)
    const b = buckets.find((bk) => h >= bk.min && h < bk.max)
    if (b) b.count += cnt
  }
  heightData.value = buckets
    .filter((b) => b.count > 0)
    .map((b) => ({ label: b.label, value: b.count, color: b.color }))
}

// ── Drought tolerance ─────────────────────────────────────────────────────────
const droughtData = ref<HBarItem[]>([])
const DROUGHT_COLORS: Record<string, string> = {
  High: '#e6a835',
  Moderate: '#4fc3f7',
  Low: '#1565c0',
  Unknown: '#555577',
}

async function loadDrought() {
  const where = buildFilterWhere()
  const res = await query(`
    SELECT
      COALESCE(se.drought_tolerance, 'Unknown') AS drought_tolerance,
      COUNT(*) AS cnt
    FROM trees t
    LEFT JOIN species_enrichment se ON t.species = se.species
    WHERE 1=1
    ${where.replace('WHERE 1=1', 'AND 1=1')}
    GROUP BY drought_tolerance
    ORDER BY cnt DESC
  `)
  droughtData.value = res.rows.map((r) => ({
    label: String(r.drought_tolerance ?? 'Unknown'),
    value: Number(r.cnt ?? 0),
    color: DROUGHT_COLORS[String(r.drought_tolerance)] ?? '#7a7a9e',
  }))
}

// ── Filter WHERE builder ──────────────────────────────────────────────────────
function buildFilterWhere(): string {
  const clauses: string[] = ['1=1']
  if (selectedCategory.value)
    clauses.push(`COALESCE(se.tree_category, 'default') = '${selectedCategory.value.replace(/'/g, "''")}'`)
  if (selectedSpecies.value)
    clauses.push(`t.species = '${selectedSpecies.value.replace(/'/g, "''")}'`)
  if (selectedNative.value)
    clauses.push(`COALESCE(se.native_status, 'Unknown') = '${selectedNative.value.replace(/'/g, "''")}'`)
  return `WHERE ${clauses.join(' AND ')}`
}

// ── Orchestration ─────────────────────────────────────────────────────────────
async function loadAll() {
  if (!dbReady.value) return
  await Promise.all([
    loadKpis(),
    loadCategories(),
    loadSpecies(),
    loadDbh(),
    loadDecades(),
    loadNative(),
    loadHeight(),
    loadDrought(),
  ])
}

// Full reload when city switches
watch([dbReady, selectedCity], () => {
  selectedCategory.value = null
  selectedSpecies.value = null
  selectedNative.value = null
  void loadAll()
})

// Partial reloads when cross-filters change
watch(selectedCategory, () => {
  void loadSpecies()
  void loadKpis()
  void loadDbh()
  void loadNative()
  void loadDecades()
  void loadHeight()
  void loadDrought()
})
watch(selectedSpecies, () => {
  void loadCategories()
  void loadKpis()
  void loadNative()
})
watch(selectedNative, () => {
  void loadSpecies()
  void loadCategories()
  void loadKpis()
})

onMounted(() => void loadAll())

// ── Formatters ────────────────────────────────────────────────────────────────
function fmtBig(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return n.toLocaleString()
}
</script>

<style scoped>
.summary-page {
  padding: 24px 28px;
  overflow-y: auto;
  height: 100%;
  box-sizing: border-box;
  background: #1a1a2e;
  color: #e0e0e0;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ── Header ── */
.summary-header {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.summary-title-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.summary-title-row h1 {
  margin: 0;
  font-size: 1.5rem;
  font-weight: 700;
  color: #e0e0e0;
}

.city-badge {
  background: rgba(79, 195, 247, 0.15);
  border: 1px solid rgba(79, 195, 247, 0.35);
  color: #4fc3f7;
  padding: 4px 12px;
  border-radius: 20px;
  font-size: 0.78rem;
  font-weight: 600;
}

.active-filters {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.filter-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(79, 195, 247, 0.12);
  border: 1px solid rgba(79, 195, 247, 0.3);
  border-radius: 16px;
  padding: 3px 10px 3px 12px;
  font-size: 0.75rem;
  color: #4fc3f7;
}

.filter-chip-clear {
  background: none;
  border: none;
  color: #4fc3f7;
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
  padding: 0;
  opacity: 0.7;
}
.filter-chip-clear:hover { opacity: 1; }

.clear-all-btn {
  background: none;
  border: none;
  color: #7a7a9e;
  cursor: pointer;
  font-size: 0.72rem;
  text-decoration: underline;
  padding: 0;
}
.clear-all-btn:hover { color: #e0e0e0; }

/* ── Loading ── */
.loading-state {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 60px 0;
  justify-content: center;
  color: #7a7a9e;
  font-size: 0.9rem;
}

.loading-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid #0f3460;
  border-top-color: #4fc3f7;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ── KPI row ── */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
}

@media (max-width: 900px) {
  .kpi-row { grid-template-columns: repeat(2, 1fr); }
}

/* ── Chart rows ── */
.chart-row {
  display: flex;
  gap: 14px;
  align-items: flex-start;
  flex-wrap: wrap;
}

.chart-card {
  background: #16213e;
  border: 1px solid #0f3460;
  border-radius: 8px;
  padding: 18px 20px;
  flex: 1;
  min-width: 260px;
}

.chart-card--narrow {
  flex: 0 0 260px;
}

.chart-card--wide {
  flex: 2;
}

.chart-card-header {
  margin-bottom: 14px;
}

.chart-card-header h2 {
  margin: 0;
  font-size: 0.9rem;
  font-weight: 600;
  color: #c0c0d8;
  letter-spacing: 0.3px;
}

.header-filter-hint {
  font-weight: 400;
  color: #7a7a9e;
  font-size: 0.8rem;
}

.header-filter-hint em {
  color: #4fc3f7;
  font-style: normal;
  text-transform: capitalize;
}

.chart-sub {
  display: block;
  font-size: 0.7rem;
  color: #555577;
  margin-top: 2px;
}
</style>
