import { afterAll, beforeAll, expect, it, vi } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { loadMissionTrees, loadTreeRanking } from '../lib/treeRankings'

let connection: DuckDBConnection
vi.mock('../lib/e2eFixtures', () => ({ e2eFixtures: () => null }))
vi.mock('../composables/useDuckDB', () => ({ useDuckDB: () => ({
  query: async (sql: string) => {
    const result = await connection.runAndReadAll(sql.replace(/read_parquet\('[^']+'\)/g, 'rankings'))
    return { rows: result.getRowObjects() }
  },
}) }))

beforeAll(async () => {
  connection = await (await DuckDBInstance.create(':memory:')).connect()
  await connection.run(`CREATE TABLE rankings AS SELECT
    'boston-' || i AS ranking_tree_id, 'USBOS' AS ranking_city,
    'Quercus rubra' AS ranking_species, 'broadleaf' AS ranking_tree_form,
    42.36 AS ranking_latitude, -71.06 AS ranking_longitude,
    'rare' AS rarity_tier, CASE WHEN i < 2 THEN 1 ELSE i END AS trunk_rank,
    NULL::BIGINT AS canopy_rank FROM range(30) t(i)`)
})
afterAll(() => connection?.closeSync())

it('executes the real catalog SQL with a bounded, deduplicated set of targets', async () => {
  const trees = await loadMissionTrees('USBOS')
  expect(trees).toHaveLength(10)
  expect(trees.filter(t => t.trunkRank === 1)).toHaveLength(2)
  expect(new Set(trees.map(t => t.treeId)).size).toBe(10)
  expect(await loadMissionTrees("USBOS' OR 1=1 --")).toEqual([])
})

it('loads a tree snapshot and returns null for an unknown ID', async () => {
  expect(await loadTreeRanking('boston-1')).toMatchObject({ city: 'USBOS', trunkRank: 1, rarityTier: 'rare' })
  expect(await loadTreeRanking("boston-1' OR 1=1 --")).toBeNull()
})
