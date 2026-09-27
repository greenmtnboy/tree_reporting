/**
 * Predict a tile on demand: fetch the NAIP imagery around a latitude/longitude,
 * run the imagery model on it and write its bundle into the tile directory.
 *
 * The work is imagery_model/src/urban_tree_ml/point_predict.py, run through uv;
 * this only validates the point, runs one prediction at a time (each one loads
 * the model), and reads the JSON summary the script prints last.
 */
import { spawn } from 'node:child_process'
import path from 'node:path'

export interface PointPredictOptions {
  /** imagery_model/, where `uv run` resolves the package. */
  modelDir: string
  /** The reviewer's tile directory; the bundle lands beside the others. */
  tileDir: string
  /** Training run id; the script defaults to the newest complete run. */
  run?: string
  /** Operating threshold the page's slider starts at. */
  threshold?: number
  /** Lowest candidate score written to the bundle. */
  minScore?: number
  timeoutMs?: number
}

export interface PointPredictTile {
  point: [number, number]
  tileId: string
  city: string
  predictions: number
  aboveThreshold: number
  inventoryTrees: number
  inventoryCities: string[]
}

export function parsePoint(body: unknown): [number, number] {
  const { lat, lon } = (body ?? {}) as { lat?: unknown; lon?: unknown }
  const latitude = Number(lat)
  const longitude = Number(lon)
  if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90) throw new Error('lat must be a latitude')
  if (!Number.isFinite(longitude) || longitude < -180 || longitude > 180) throw new Error('lon must be a longitude')
  return [latitude, longitude]
}

export function pointPredictArgs(point: [number, number], options: PointPredictOptions): string[] {
  const args = [
    'run', '--group', 'imagery', '--group', 'train',
    'python', '-m', 'urban_tree_ml.point_predict',
    '--point', `${point[0]},${point[1]}`,
    '--out', path.resolve(options.tileDir),
  ]
  if (options.run) args.push('--run', options.run)
  if (options.threshold != null) args.push('--threshold', String(options.threshold))
  if (options.minScore != null) args.push('--min-score', String(options.minScore))
  return args
}

let queue: Promise<unknown> = Promise.resolve()

/** Run one point prediction; calls queue behind each other. */
export function predictPoint(point: [number, number], options: PointPredictOptions): Promise<PointPredictTile> {
  const next = queue.then(() => runOnce(point, options))
  queue = next.catch(() => undefined)
  return next
}

function runOnce(point: [number, number], options: PointPredictOptions): Promise<PointPredictTile> {
  return new Promise((resolve, reject) => {
    const child = spawn('uv', pointPredictArgs(point, options), {
      cwd: options.modelDir,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      windowsHide: true,
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    const timer = setTimeout(() => child.kill(), options.timeoutMs ?? 5 * 60_000)
    child.on('error', (error) => { clearTimeout(timer); reject(error) })
    child.on('close', (code) => {
      clearTimeout(timer)
      const lastLine = stdout.trim().split('\n').pop() ?? ''
      const tail = stderr.trim().split('\n').slice(-3).join(' | ')
      try {
        const summary = JSON.parse(lastLine) as { tiles: PointPredictTile[] }
        const tile = summary.tiles[0]
        if (tile) return resolve(tile)
      } catch {
        // fall through to the error below
      }
      reject(new Error(`point prediction failed (exit ${code}): ${tail || 'no output'}`))
    })
  })
}
