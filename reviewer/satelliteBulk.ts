import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

// Bulk prediction sets: imagery_model/artifacts/bulk/<set>/, written by
// urban_tree_ml.bulk_predict.  The map reads each set's overview.json (tile
// footprints, every detection with its merge fate, the city's published
// trees); `bulk_predict overview` rewrites it after each predict batch.

export interface BulkSet {
  set: string
  city: string
  run: string
  plannedTiles: number
  overviewAt: string | null
}

export function bulkDir(modelDir: string): string {
  return path.join(modelDir, 'artifacts', 'bulk')
}

export function listBulkSets(modelDir: string, city?: string): BulkSet[] {
  const root = bulkDir(modelDir)
  if (!existsSync(root)) return []
  const sets: BulkSet[] = []
  for (const name of readdirSync(root).sort()) {
    const planPath = path.join(root, name, 'plan.json')
    if (!existsSync(planPath)) continue
    let plan: { city?: unknown; run?: unknown; tiles?: unknown }
    try {
      plan = JSON.parse(readFileSync(planPath, 'utf8'))
    } catch {
      continue
    }
    if (typeof plan.city !== 'string' || typeof plan.run !== 'string') continue
    if (city && plan.city.toUpperCase() !== city.toUpperCase()) continue
    const overview = path.join(root, name, 'overview.json')
    sets.push({
      set: name,
      city: plan.city.toUpperCase(),
      run: plan.run,
      plannedTiles: Array.isArray(plan.tiles) ? plan.tiles.length : 0,
      overviewAt: existsSync(overview) ? statSync(overview).mtime.toISOString() : null,
    })
  }
  return sets
}

// The overview file of a listed set, or null; the name must be one the
// listing returns, so a request never reaches outside the bulk directory.
export function bulkOverviewPath(modelDir: string, set: string): string | null {
  const found = listBulkSets(modelDir).find((s) => s.set === set)
  if (!found?.overviewAt) return null
  return path.join(bulkDir(modelDir), found.set, 'overview.json')
}
