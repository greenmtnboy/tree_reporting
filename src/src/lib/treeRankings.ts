import { useDuckDB } from '../composables/useDuckDB'
import { e2eFixtures } from './e2eFixtures'
import { REMOTE_TREES_BASE_URL, TREE_DATA_VERSION } from '../workers/parquetUrls'
import type { RankedTree, RarityTier } from './missions'

const SOURCE = `read_parquet('${REMOTE_TREES_BASE_URL}/tree_rankings_v${TREE_DATA_VERSION}.parquet')`
const quote = (s: string) => `'${s.replace(/'/g, "''")}'`

function fromRow(row: Record<string, unknown>): RankedTree {
  return {
    treeId: String(row.ranking_tree_id), city: String(row.ranking_city),
    species: String(row.ranking_species), treeForm: row.ranking_tree_form as string | null,
    lat: Number(row.ranking_latitude), lng: Number(row.ranking_longitude),
    rarityTier: row.rarity_tier as RarityTier | null,
    trunkRank: row.trunk_rank == null ? null : Number(row.trunk_rank),
    canopyRank: row.canopy_rank == null ? null : Number(row.canopy_rank),
  }
}

export async function loadMissionTrees(city: string): Promise<RankedTree[]> {
  const fixture = e2eFixtures()
  if (fixture) {
    if (fixture.rankingsUnavailable) throw new Error('Rankings unavailable')
    return (fixture.rankings ?? []).filter(t => t.city === city)
  }
  const { rows } = await useDuckDB().query(`
    WITH city_trees AS (
      SELECT * FROM ${SOURCE} WHERE ranking_city = ${quote(city)}
        AND ranking_latitude IS NOT NULL AND ranking_longitude IS NOT NULL
    ), targets AS (
      SELECT *, unnest(list_filter([
        CASE WHEN trunk_rank = 1 THEN 'trunk' END,
        CASE WHEN canopy_rank = 1 THEN 'canopy' END,
        rarity_tier,
        CASE WHEN ranking_tree_form IN ('palm', 'conifer') THEN ranking_tree_form END
      ], x -> x IS NOT NULL)) AS mission FROM city_trees
    )
    SELECT DISTINCT * EXCLUDE (mission) FROM targets
    QUALIFY row_number() OVER (PARTITION BY mission ORDER BY ranking_tree_id) <= 10
  `)
  return rows.map(fromRow)
}

export async function loadTreeRanking(treeId: string): Promise<RankedTree | null> {
  const fixture = e2eFixtures()
  if (fixture) return fixture.rankings?.find(t => t.treeId === treeId) ?? null
  const { rows } = await useDuckDB().query(`SELECT * FROM ${SOURCE} WHERE ranking_tree_id = ${quote(treeId)} LIMIT 1`)
  return rows[0] ? fromRow(rows[0]) : null
}
