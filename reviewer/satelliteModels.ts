/**
 * The model runs the satellite page can pick from, and exporting a run's
 * evaluation cohort into the tile directory as a new tile set.
 *
 * A run is a directory under imagery_model/artifacts/runs with a COMPLETE
 * marker; its cohorts are evaluation/<cohort>/evaluation-metadata.json.  The
 * export is imagery_model/src/urban_tree_ml/tile_bundle_export.py run through
 * uv; this resolves the mosaic it reads and reports the script's progress.
 */
import { spawn } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import path from 'node:path'

export interface RunCohort {
  cohort: string
  city: string
  split: string
  /** Chips with predictions in this cohort. */
  chips: number | null
}

export interface ModelRun {
  runId: string
  completedAt: string
  /** The checkpoint the evaluations used is on disk, so point predictions can load it. */
  checkpoint: boolean
  confidenceThreshold: number | null
  cohorts: RunCohort[]
}

function readJson(file: string): Record<string, any> {
  return JSON.parse(readFileSync(file, 'utf8'))
}

function runsDir(modelDir: string): string {
  return path.join(modelDir, 'artifacts', 'runs')
}

/** Complete runs, newest first (the order point_predict's default uses). */
export function listRuns(modelDir: string): ModelRun[] {
  const dir = runsDir(modelDir)
  if (!existsSync(dir)) return []
  const runs: (ModelRun & { mtimeMs: number })[] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const runDir = path.join(dir, entry.name)
    const marker = path.join(runDir, 'COMPLETE')
    if (!existsSync(marker)) continue
    const evaluationDir = path.join(runDir, 'evaluation')
    const cohorts: RunCohort[] = []
    let checkpoint = false
    let threshold: number | null = null
    if (existsSync(evaluationDir)) {
      for (const cohort of readdirSync(evaluationDir, { withFileTypes: true })) {
        const metadataPath = path.join(evaluationDir, cohort.name, 'evaluation-metadata.json')
        if (!cohort.isDirectory() || !existsSync(metadataPath)) continue
        try {
          const metadata = readJson(metadataPath)
          // The exporter refuses sealed test chips for the public reviewer.
          if (metadata.split === 'test') continue
          const coverage = path.join(evaluationDir, cohort.name, 'inference-coverage.json')
          let chips: number | null = null
          if (existsSync(coverage)) {
            const c = readJson(coverage)
            chips = typeof c.chip_count === 'number' ? c.chip_count : null
          }
          cohorts.push({
            cohort: cohort.name,
            city: String(metadata.city ?? metadata.config?.inventory?.city ?? '').toUpperCase(),
            split: String(metadata.split ?? ''),
            chips,
          })
          if (metadata.checkpoint) checkpoint ||= existsSync(path.join(runDir, 'checkpoints', path.basename(String(metadata.checkpoint))))
          threshold ??= metadata.config?.evaluation?.confidence_threshold ?? null
        } catch {
          // a half-written cohort is skipped, not fatal to the listing
        }
      }
    }
    cohorts.sort((a, b) => a.city.localeCompare(b.city) || a.cohort.localeCompare(b.cohort))
    const mtimeMs = statSync(marker).mtimeMs
    runs.push({ runId: entry.name, completedAt: new Date(mtimeMs).toISOString(), checkpoint, confidenceThreshold: threshold, cohorts, mtimeMs })
  }
  return runs.sort((a, b) => b.mtimeMs - a.mtimeMs).map(({ mtimeMs: _, ...run }) => run)
}

/**
 * The mosaic a cohort's chips were cut from (a VRT, or one GeoTIFF where a
 * cohort reads a single NAIP item).  Each run names its own VRT, but
 * runs over the same NAIP items at the same resolution share one mosaic, and
 * the workstation usually holds only the VRT of whichever run first fetched
 * it.  So: the run's own VRT when present, else any local VRT whose manifest
 * lists exactly the run's items at its resolution (or, when the config lists
 * none, one cut from the same base mosaic); null leaves the exporter's
 * default (which then names what is missing).
 */
export function resolveMosaic(modelDir: string, runId: string, cohort: string): string | null {
  const metadata = readJson(path.join(runsDir(modelDir), runId, 'evaluation', cohort, 'evaluation-metadata.json'))
  const imagery = metadata.config?.imagery ?? {}
  const city = String(metadata.city ?? metadata.config?.inventory?.city ?? '').toLowerCase()
  const cityDir = path.join(modelDir, 'artifacts', 'imagery', city)
  if (!city || !existsSync(cityDir)) return null
  const vrts: string[] = []
  for (const year of readdirSync(cityDir, { withFileTypes: true })) {
    if (!year.isDirectory()) continue
    for (const f of readdirSync(path.join(cityDir, year.name))) {
      if (/\.(vrt|tif)$/.test(f)) vrts.push(path.join(cityDir, year.name, f))
    }
  }
  const manifestOf = (raster: string) => raster.replace(/\.(vrt|tif)$/, '.manifest.json')
  const withManifest = vrts.filter((v) => existsSync(manifestOf(v)))
  const own = path.basename(String(imagery.local_raster ?? ''))
  const exact = withManifest.find((v) => path.basename(v) === own)
  if (exact) return exact
  const items = [...(imagery.item_ids ?? [])].map(String).sort().join('\n')
  const resolution = Number(imagery.resolution_m)
  const matches = (v: string, sameSources: (sources: string) => boolean) => {
    try {
      const manifest = readJson(manifestOf(v))
      const sources = (manifest.sources ?? []).map((s: { item_id?: unknown }) => String(s.item_id)).sort().join('\n')
      const res = manifest.raster?.resolution?.[0]
      return sameSources(sources) && (!Number.isFinite(resolution) || Math.abs(Number(res) - resolution) < 1e-6)
    } catch {
      return false
    }
  }
  if (items) return withManifest.find((v) => matches(v, (sources) => sources === items)) ?? null
  // A config without its item list (San Francisco's) still names the base
  // mosaic its run VRT was cut from: <base>-<runId>-<suffix>.vrt.  Another
  // run's VRT over the same base at the same resolution is the same grid.
  const [base, suffix] = own.split(`-${runId}-`)
  if (!base || suffix == null) return null
  return withManifest.find((v) => {
    const name = path.basename(v)
    return name.startsWith(`${base}-`) && name.endsWith(`-${suffix}`) && matches(v, (sources) => sources.length > 0)
  }) ?? null
}

export interface ExportRequest {
  runId: string
  cohort: string
  /** Tile-set directory name under the tile directory. */
  set: string
  threshold?: number
  minScore?: number
  /** Only these chips of the cohort; absent, every chip with predictions. */
  chips?: string[]
}

function parseUnit(b: Record<string, unknown>, key: string): number | undefined {
  if (b[key] == null || b[key] === '') return undefined
  const n = Number(b[key])
  if (!Number.isFinite(n) || n < 0 || n > 1) throw new Error(`${key} must be between 0 and 1`)
  return n
}

function parseSet(value: unknown): string {
  const set = String(value ?? '').trim()
  if (!/^[A-Za-z0-9._-]{1,120}$/.test(set) || set.startsWith('.')) throw new Error('set must be a plain directory name')
  return set
}

export function parseExportRequest(body: unknown, runs: ModelRun[]): ExportRequest {
  const b = (body ?? {}) as Record<string, unknown>
  const run = runs.find((r) => r.runId === b.runId)
  if (!run) throw new Error(`${String(b.runId)} is not a complete run`)
  if (!run.cohorts.some((c) => c.cohort === b.cohort)) throw new Error(`${run.runId} has no exportable cohort ${String(b.cohort)}`)
  return { runId: run.runId, cohort: String(b.cohort), set: parseSet(b.set), threshold: parseUnit(b, 'threshold'), minScore: parseUnit(b, 'minScore') }
}

export function exportArgs(request: ExportRequest, tileDir: string, mosaic: string | null, crownCoefficients: string | null): string[] {
  const args = [
    'run', '--group', 'imagery',
    'python', '-m', 'urban_tree_ml.tile_bundle_export',
    '--run', request.runId,
    '--cohort', request.cohort,
    '--out', path.resolve(tileDir, request.set),
    '--min-score', String(request.minScore ?? 0.1),
  ]
  if (request.threshold != null) args.push('--threshold', String(request.threshold))
  if (mosaic) args.push('--raster', mosaic)
  if (crownCoefficients) args.push('--crown-coefficients', crownCoefficients)
  if (request.chips?.length) args.push('--chips', ...request.chips)
  return args
}

// ---------------------------------------------------------------------------
// Exporting an area: the run's chips under a map view, across its cohorts
// ---------------------------------------------------------------------------

/** More chips than this in one area export is a zoomed-out click, not a block to curate. */
export const MAX_AREA_CHIPS = 300

export interface AreaRequest {
  runId: string
  city: string
  /** west, south, east, north in degrees. */
  bbox: [number, number, number, number]
  threshold?: number
  minScore?: number
  /** Plan only: report what would be exported. */
  dryRun: boolean
}

export function parseAreaRequest(body: unknown, runs: ModelRun[]): AreaRequest {
  const b = (body ?? {}) as Record<string, unknown>
  const run = runs.find((r) => r.runId === b.runId)
  if (!run) throw new Error(`${String(b.runId)} is not a complete run`)
  const city = String(b.city ?? '').toUpperCase()
  if (!/^[A-Z]{5}$/.test(city)) throw new Error('city must be a five-letter city code')
  if (!run.cohorts.some((c) => c.city === city)) throw new Error(`${run.runId} has no ${city} cohort`)
  const bbox = Array.isArray(b.bbox) ? b.bbox.map(Number) : []
  const [west, south, east, north] = bbox
  if (bbox.length !== 4 || bbox.some((v) => !Number.isFinite(v)) || west >= east || south >= north
    || Math.abs(south) > 90 || Math.abs(north) > 90 || Math.abs(west) > 180 || Math.abs(east) > 180) {
    throw new Error('bbox must be [west, south, east, north] in degrees')
  }
  return { runId: run.runId, city, bbox: [west, south, east, north], threshold: parseUnit(b, 'threshold'), minScore: parseUnit(b, 'minScore'), dryRun: Boolean(b.dryRun) }
}

/** The tile set an area export writes to: one per city and run, growing with each export. */
export function areaSetName(city: string, runId: string): string {
  return `${city.toLowerCase()}-${(runId.match(/\d+cm-v\d+.*$/) || [runId])[0]}-areas`
}

/** The run's chips in the box, by cohort (scripts/chips_in_area.py, in the imagery model's environment). */
export function chipsInArea(modelDir: string, request: AreaRequest, mosaic: string): Promise<Record<string, string[]>> {
  const script = path.join(import.meta.dirname, 'scripts', 'chips_in_area.py')
  const args = ['run', '--group', 'imagery', 'python', script, '--run', request.runId, '--city', request.city,
    `--bbox=${request.bbox.join(',')}`, '--raster', mosaic]
  return new Promise((resolve, reject) => {
    const child = spawn('uv', args, { cwd: modelDir, env: { ...process.env, PYTHONIOENCODING: 'utf-8' }, windowsHide: true })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    child.on('error', reject)
    child.on('close', (code) => {
      try {
        const parsed = JSON.parse(stdout.trim().split('\n').pop() ?? '') as { cohorts: Record<string, string[]> }
        if (code === 0) return resolve(parsed.cohorts)
      } catch {
        // fall through
      }
      reject(new Error(`finding chips failed (exit ${code}): ${stderr.trim().split('\n').slice(-2).join(' | ') || 'no output'}`))
    })
  })
}

export interface AreaPlan {
  set: string
  /** New chips by cohort, the ones already exported for this run left out. */
  cohorts: Record<string, string[]>
  newChips: number
  alreadyExported: number
}

/** Drop the chips this run already has tiles for; refuse an area too large to curate. */
export function planArea(found: Record<string, string[]>, exportedChips: ReadonlySet<string>, request: AreaRequest): AreaPlan {
  const cohorts: Record<string, string[]> = {}
  let newChips = 0
  let alreadyExported = 0
  const seen = new Set<string>()
  for (const [cohort, chips] of Object.entries(found)) {
    const fresh = chips.filter((c) => {
      if (exportedChips.has(c)) { alreadyExported += 1; return false }
      if (seen.has(c)) return false
      seen.add(c)
      return true
    })
    if (fresh.length) cohorts[cohort] = fresh
    newChips += fresh.length
  }
  if (newChips > MAX_AREA_CHIPS) throw new Error(`${newChips} chips in view; zoom in to at most ${MAX_AREA_CHIPS}`)
  return { set: areaSetName(request.city, request.runId), cohorts, newChips, alreadyExported }
}

export function areaExportRequests(plan: AreaPlan, request: AreaRequest): ExportRequest[] {
  return Object.entries(plan.cohorts).map(([cohort, chips]) => ({
    runId: request.runId, cohort, set: plan.set, threshold: request.threshold, minScore: request.minScore, chips,
  }))
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export interface ExportJob {
  id: number
  /** The first step, for display; `steps` holds every cohort's. */
  request: ExportRequest
  steps: ExportRequest[]
  status: 'running' | 'done' | 'failed'
  startedAt: string
  finishedAt: string | null
  /** Tiles written so far (one "<tileId>: N predictions" line each). */
  exported: number
  /** Chips to export, when known. */
  total: number | null
  mosaic: string | null
  log: string[]
  error: string | null
}

/** One export at a time: each reads the whole mosaic and the inventory parquet. */
export class ExportJobs {
  private jobs: ExportJob[] = []
  private nextId = 1

  constructor(private readonly modelDir: string, private readonly tileDir: string, private readonly crownCoefficients: string | null = null) {}

  list(): ExportJob[] {
    return [...this.jobs].reverse()
  }

  get(id: number): ExportJob | undefined {
    return this.jobs.find((j) => j.id === id)
  }

  get running(): boolean {
    return this.jobs.some((j) => j.status === 'running')
  }

  /** Run the steps one after another; the job fails at the first step that does. */
  start(steps: ExportRequest | ExportRequest[], total: number | null): ExportJob {
    const list = Array.isArray(steps) ? steps : [steps]
    if (!list.length) throw new Error('Nothing to export')
    if (this.running) throw new Error('An export is already running')
    const job: ExportJob = {
      id: this.nextId++, request: list[0], steps: list, status: 'running', startedAt: new Date().toISOString(), finishedAt: null,
      exported: 0, total, mosaic: resolveMosaic(this.modelDir, list[0].runId, list[0].cohort), log: [], error: null,
    }
    this.jobs.push(job)
    const finish = (error: string | null) => {
      job.status = error ? 'failed' : 'done'
      job.error = error
      job.finishedAt = new Date().toISOString()
    }
    const runStep = (index: number) => {
      if (index >= list.length) return finish(null)
      const request = list[index]
      const mosaic = index === 0 ? job.mosaic : resolveMosaic(this.modelDir, request.runId, request.cohort)
      const child = spawn('uv', exportArgs(request, this.tileDir, mosaic, this.crownCoefficients), {
        cwd: this.modelDir,
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
        windowsHide: true,
      })
      const onData = (chunk: Buffer) => {
        for (const line of chunk.toString('utf8').split(/\r?\n/)) {
          if (!line.trim()) continue
          if (/: \d+ predictions/.test(line)) job.exported += 1
          job.log.push(line)
          if (job.log.length > 40) job.log.shift()
        }
      }
      child.stdout.on('data', onData)
      child.stderr.on('data', onData)
      let failed = false
      child.on('error', (error) => { failed = true; finish(error.message) })
      child.on('close', (code) => {
        if (failed) return
        if (code !== 0) return finish(`${request.cohort}: exit ${code}: ${job.log.slice(-3).join(' | ')}`)
        runStep(index + 1)
      })
    }
    runStep(0)
    return job
  }
}
