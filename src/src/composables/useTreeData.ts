import { ref } from 'vue'
import type { TreeCategory } from '../types'
import { getTreeCategory } from './useTreeCategories'

export interface TreeGeoJSON {
  type: 'FeatureCollection'
  features: GeoJSON.Feature<GeoJSON.Point>[]
}

export interface SpeciesEnrichment {
  species: string
  tree_category: string | null
  icon_rgba_b64: string | null
  icon_width: number | null
  icon_height: number | null
  native_status: string | null
  is_evergreen: boolean | null
  mature_height_ft: number | null
  bloom_season: string | null
  wildlife_value: string | null
  fire_risk: string | null
}

export interface CategoryIconData {
  category: TreeCategory
  rgbaBase64: string
  width: number
  height: number
}

export interface TreeQueryRow {
  tree_id: string
  species: string
  latitude: number
  longitude: number
  diameter_at_breast_height: number | null
}

// ~1 meter jitter so co-located trees spread slightly without drifting into streets
const JITTER = 0.00001

export function jitterCoord(coord: number): number {
  return coord + (Math.random() - 0.5) * 2 * JITTER
}

// Small lean angle (±10°) for natural variation
export function randomRotation(): number {
  return (Math.random() - 0.5) * 20
}

// Per-tree size multiplier (0.7–1.3) so same-species trees are visually distinct
export function randomSizeScale(): number {
  return 0.7 + Math.random() * 0.6
}

function normalizeSpecies(species: string): string {
  return species.trim().toLowerCase()
}

function isTreeCategory(value: string): value is TreeCategory {
  return value === 'palm'
    || value === 'broadleaf'
    || value === 'spreading'
    || value === 'coniferous'
    || value === 'columnar'
    || value === 'ornamental'
    || value === 'default'
}

function buildSpeciesLookup(rows: SpeciesEnrichment[]): Map<string, SpeciesEnrichment> {
  const lookup = new Map<string, SpeciesEnrichment>()
  for (const row of rows) {
    if (!row.species) continue
    lookup.set(normalizeSpecies(row.species), row)
  }
  return lookup
}

function buildCategoryIcons(rows: SpeciesEnrichment[]): CategoryIconData[] {
  const categoryIcons = new Map<TreeCategory, CategoryIconData>()
  for (const row of rows) {
    if (!row.tree_category || !isTreeCategory(row.tree_category)) continue
    if (!row.icon_rgba_b64 || !row.icon_width || !row.icon_height) continue
    if (categoryIcons.has(row.tree_category)) continue
    categoryIcons.set(row.tree_category, {
      category: row.tree_category,
      rgbaBase64: row.icon_rgba_b64,
      width: row.icon_width,
      height: row.icon_height,
    })
  }
  return [...categoryIcons.values()]
}

export function useTreeData() {
  const categoryIcons = ref<CategoryIconData[]>([])
  const speciesLookup = ref<Map<string, SpeciesEnrichment>>(new Map())
  const loading = ref(true)
  const error = ref<string | null>(null)

  async function load() {
    try {
      const speciesRes = await fetch(import.meta.env.BASE_URL + 'data/species_data.json').catch(() => null)
      let species: SpeciesEnrichment[] = []
      if (speciesRes?.ok) {
        species = await speciesRes.json()
      }
      speciesLookup.value = buildSpeciesLookup(species)
      categoryIcons.value = buildCategoryIcons(species)
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  function buildTreeGeoJSON(rows: TreeQueryRow[]): TreeGeoJSON {
    const valid = rows.filter((t) => t.latitude && t.longitude)

    const locCounts = new Map<string, number>()
    for (const t of valid) {
      const key = `${t.latitude},${t.longitude}`
      locCounts.set(key, (locCounts.get(key) || 0) + 1)
    }

    const features: GeoJSON.Feature<GeoJSON.Point>[] = valid.map((tree) => {
      const enrichment = speciesLookup.value.get(normalizeSpecies(tree.species || ''))
      const fallback = getTreeCategory(tree.species || '')
      const category = enrichment?.tree_category && isTreeCategory(enrichment.tree_category)
        ? enrichment.tree_category
        : fallback.category
      const color = fallback.color
      const key = `${tree.latitude},${tree.longitude}`
      const isCoLocated = (locCounts.get(key) || 0) > 1

      return {
        type: 'Feature' as const,
        geometry: {
          type: 'Point' as const,
          coordinates: isCoLocated
            ? [jitterCoord(tree.longitude), jitterCoord(tree.latitude)]
            : [tree.longitude, tree.latitude],
        },
        properties: {
          id: tree.tree_id,
          dbh: tree.diameter_at_breast_height ?? 3,
          category,
          color,
          rotation: isCoLocated ? randomRotation() : 0,
          sizeScale: randomSizeScale(),
        },
      }
    })

    return { type: 'FeatureCollection', features }
  }

  function getSpeciesEnrichment(species: string | null | undefined): SpeciesEnrichment | undefined {
    if (!species) return undefined
    return speciesLookup.value.get(normalizeSpecies(species))
  }

  load()
  return { categoryIcons, loading, error, buildTreeGeoJSON, getSpeciesEnrichment }
}
