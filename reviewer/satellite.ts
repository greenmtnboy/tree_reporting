/**
 * Satellite (aerial-imagery) review: tiles in, reviewed detections out.
 *
 * The page at /satellite loads a tile bundle -- the NAIP chip, the model's
 * detections on it, and the published inventory trees it covers, each with a
 * DBH-derived crown width -- and lets a reviewer decide, per detection,
 * whether it is a new tree, a duplicate of an inventory tree, not a tree, or
 * uncertain.  Decisions are private Firestore records; **publishing** is the
 * gate between them and the public export the data pipeline reads
 * (`satellite/published_trees.ndjson` + `satellite/manifest.json`), exactly
 * as approval is for photo submissions in server.ts.
 *
 * Bundles are produced by `imagery_model/src/urban_tree_ml/tile_bundle_export.py`
 * and read from `SATELLITE_TILE_DIR` (default: reviewer/fixtures/tiles).
 * The contract is `TilePredictionBundle` below; `assertBundle` refuses what
 * the exporter's `validate_bundle` refuses, and the two are kept in step.
 *
 * Three rules the storage encodes, from PREDICTION_CURATION_HANDOFF.md:
 *
 *  - A rejection or an "uncertain" is a statement about this image, never
 *    about the inventory: those decisions are stored and never published,
 *    and no decision can delete or edit a canonical tree.
 *  - A model estimate is never promoted to a measurement.  The export carries
 *    `predictedDbhInches` and `measuredDbhInches` separately; the ingest
 *    (`data/raw/satellite_tree_info.py`) publishes only the measured one.
 *  - A duplicate is a link the reviewer made, not a merge the model made.
 *    It is exported at the linked tree's coordinates so the shared cluster
 *    merge (`data/raw/tree_dedup.preql`) is guaranteed to absorb it into
 *    that tree, with the municipal values winning and the satellite id kept
 *    in `merged_tree_ids`.
 */

import express from 'express'
import { createHash } from 'node:crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { FieldValue, Timestamp, type Firestore } from 'firebase-admin/firestore'

export const BUNDLE_SCHEMA_VERSION = 1
export const EXPORT_SCHEMA_VERSION = 1
export const SATELLITE_EXPORT_PATH = 'satellite/published_trees.ndjson'
export const SATELLITE_MANIFEST_PATH = 'satellite/manifest.json'
export const OBSERVATIONS = 'satelliteObservations'
export const PUBLISHED = 'satelliteTrees'
export const TILE_REVIEWS = 'satelliteTileReviews'
const NEAREST_LIMIT = 6
const MAX_NOTES = 500
const MAX_SPECIES = 120
const EARTH_RADIUS_M = 6_371_008.8

// ---------------------------------------------------------------------------
// The bundle contract
// ---------------------------------------------------------------------------

export interface BundlePrediction {
  predictionId: string
  xPx: number
  yPx: number
  longitude: number
  latitude: number
  positionRole: 'crown_center'
  centerConfidence: number
  species: string | null
  speciesConfidence: number | null
  genus: string | null
  genusConfidence?: number | null
  dbhInches: number | null
  crownWidthM?: number | null
  crownWidthMethod?: string | null
  crownFit?: { level: string; taxon: string; n: number } | null
  candidateTreeIds?: string[]
  speciesTop?: (string | null)[]
}

export interface BundleInventoryTree {
  treeId: string
  longitude: number
  latitude: number
  species: string | null
  dbhInches: number | null
  source: string
  positionRole: 'trunk' | 'unknown' | 'crown_center'
  predictedCrownWidthM?: number | null
  crownModelLevel?: string | null
  crownDbhSource?: string | null
  mergedSources?: string | null
  mergedTreeIds?: string | null
}

export interface TilePredictionBundle {
  schemaVersion: 1
  tileId: string
  city: string
  chipId?: string
  imageryVersion: string
  provider: string
  acquisitionStart: string
  acquisitionEnd: string
  sourceItemIds: string[]
  attribution?: string
  image: {
    url: string
    sha256: string
    width: number
    height: number
    resolutionM: number
    crs: string
    affine: number[]
    /** lon = a*x + b*y + c; lat = d*x + e*y + f, tile-local pixels. */
    pixelToLonLat: number[]
    bounds?: { west: number; east: number; south: number; north: number }
  }
  predictionLayer: {
    runId: string
    checkpointSha256: string
    taxonomyVersion: string
    confidenceThreshold?: number
    cohort?: string
    status: 'available' | 'unavailable'
    predictions: BundlePrediction[]
  }
  inventoryVersion: string
  inventoryTrees: BundleInventoryTree[]
  exportedAt?: string
}

function fail(message: string): never {
  throw new Error(message)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

/** Refuse a bundle the page could not draw correctly; mirrors validate_bundle in the exporter. */
export function assertBundle(value: unknown): TilePredictionBundle {
  if (typeof value !== 'object' || value === null) fail('bundle is not an object')
  const b = value as Record<string, unknown>
  if (b.schemaVersion !== BUNDLE_SCHEMA_VERSION) fail(`bundle schemaVersion ${String(b.schemaVersion)} is not ${BUNDLE_SCHEMA_VERSION}`)
  for (const key of ['tileId', 'city', 'imageryVersion', 'provider', 'acquisitionStart', 'acquisitionEnd', 'inventoryVersion']) {
    if (typeof b[key] !== 'string' || !b[key]) fail(`bundle is missing ${key}`)
  }
  if (!/^[A-Z]{5}$/.test(b.city as string)) fail(`bundle city ${String(b.city)} is not a five-letter code`)
  if (!/^[A-Za-z0-9._:-]+$/.test(b.tileId as string)) fail(`bundle tileId ${String(b.tileId)} has characters a filename cannot`)
  const image = b.image as Record<string, unknown> | undefined
  if (!image || typeof image !== 'object') fail('bundle has no image')
  for (const key of ['width', 'height', 'resolutionM']) {
    if (!isFiniteNumber(image[key]) || (image[key] as number) <= 0) fail(`image.${key} is not a positive number`)
  }
  for (const key of ['affine', 'pixelToLonLat']) {
    const coefficients = image[key]
    if (!Array.isArray(coefficients) || coefficients.length !== 6 || !coefficients.every(isFiniteNumber)) {
      fail(`image.${key} must be six finite coefficients (a, b, c, d, e, f)`)
    }
  }
  if (typeof image.url !== 'string' || typeof image.crs !== 'string' || typeof image.sha256 !== 'string') fail('image needs url, crs and sha256')
  const layer = b.predictionLayer as Record<string, unknown> | undefined
  if (!layer || (layer.status !== 'available' && layer.status !== 'unavailable')) fail('predictionLayer.status must be available or unavailable')
  if (typeof layer.runId !== 'string' || !layer.runId) fail('predictionLayer.runId is missing')
  const predictions = Array.isArray(layer.predictions) ? (layer.predictions as unknown[]) : []
  const seen = new Set<string>()
  for (const p of predictions) {
    const pred = p as Record<string, unknown>
    if (typeof pred.predictionId !== 'string' || !pred.predictionId) fail('a prediction has no predictionId')
    if (seen.has(pred.predictionId)) fail(`prediction id ${pred.predictionId} repeats within the tile`)
    seen.add(pred.predictionId)
    for (const key of ['xPx', 'yPx', 'longitude', 'latitude', 'centerConfidence']) {
      if (!isFiniteNumber(pred[key])) fail(`prediction ${pred.predictionId} has no numeric ${key}`)
    }
    if (pred.positionRole !== 'crown_center') fail(`prediction ${pred.predictionId} positionRole must be crown_center`)
  }
  const trees = Array.isArray(b.inventoryTrees) ? (b.inventoryTrees as unknown[]) : fail('inventoryTrees must be an array')
  for (const t of trees) {
    const tree = t as Record<string, unknown>
    if (typeof tree.treeId !== 'string' || !tree.treeId) fail('an inventory tree has no treeId')
    if (!isFiniteNumber(tree.longitude) || !isFiniteNumber(tree.latitude)) fail(`inventory tree ${tree.treeId} has no coordinates`)
    if (!['trunk', 'unknown', 'crown_center'].includes(tree.positionRole as string)) fail(`inventory tree ${tree.treeId} has no positionRole`)
  }
  return value as TilePredictionBundle
}

// ---------------------------------------------------------------------------
// Geometry
// ---------------------------------------------------------------------------

export function haversineM(lon1: number, lat1: number, lon2: number, lat2: number): number {
  const p1 = (lat1 * Math.PI) / 180
  const p2 = (lat2 * Math.PI) / 180
  const dphi = p2 - p1
  const dlmb = ((lon2 - lon1) * Math.PI) / 180
  const h = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlmb / 2) ** 2
  return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(h))
}

/** Tile-local pixel to (longitude, latitude) through the bundle's fitted affine. */
export function pixelToLonLat(bundle: TilePredictionBundle, xPx: number, yPx: number): { longitude: number; latitude: number } {
  const [a, b, c, d, e, f] = bundle.image.pixelToLonLat
  return { longitude: a * xPx + b * yPx + c, latitude: d * xPx + e * yPx + f }
}

/** The inverse: (longitude, latitude) to a tile-local pixel, for drawing inventory trees. */
export function lonLatToPixel(bundle: TilePredictionBundle, longitude: number, latitude: number): { xPx: number; yPx: number } {
  const [a, b, c, d, e, f] = bundle.image.pixelToLonLat
  const det = a * e - b * d
  if (!det) throw new Error('pixelToLonLat affine is singular')
  const dx = longitude - c
  const dy = latitude - f
  return { xPx: (e * dx - b * dy) / det, yPx: (a * dy - d * dx) / det }
}

export interface NearestTree {
  treeId: string
  distanceM: number
  species: string | null
  dbhInches: number | null
  source: string
  positionRole: string
  predictedCrownWidthM: number | null
}

/** Inventory trees nearest a point, nearest first.  Suggestions, never a merge. */
export function nearestInventory(
  bundle: TilePredictionBundle,
  longitude: number,
  latitude: number,
  limit = NEAREST_LIMIT,
): NearestTree[] {
  return bundle.inventoryTrees
    .map((tree) => ({
      treeId: tree.treeId,
      distanceM: haversineM(longitude, latitude, tree.longitude, tree.latitude),
      species: tree.species ?? null,
      dbhInches: tree.dbhInches ?? null,
      source: tree.source,
      positionRole: tree.positionRole,
      predictedCrownWidthM: tree.predictedCrownWidthM ?? null,
    }))
    .sort((x, y) => x.distanceM - y.distanceM)
    .slice(0, limit)
}

// ---------------------------------------------------------------------------
// Observations: what a reviewer decided about one detection
// ---------------------------------------------------------------------------

export type Decision = 'accept' | 'duplicate' | 'reject' | 'uncertain'
export const DECISIONS: readonly Decision[] = ['accept', 'duplicate', 'reject', 'uncertain']
/** Decisions that publish.  A rejection or an uncertain is about this image only. */
export const PUBLISHABLE: ReadonlySet<Decision> = new Set<Decision>(['accept', 'duplicate'])

export interface ObservationInput {
  decision: Decision
  /** The confirmed species; null when the reviewer could not say. */
  species?: string | null
  /** 'model' when the reviewer kept the model's label, 'reviewer' when they typed one. */
  speciesSource?: 'model' | 'reviewer' | null
  measuredDbhInches?: number | null
  duplicateOfTreeId?: string | null
  /** A nudged crown centre, tile-local pixels; the trunk is never moved. */
  crownCenter?: { xPx: number; yPx: number } | null
  notes?: string | null
  /** Optimistic concurrency: the revision the client last saw, or null for a first write. */
  baseRevision?: number | null
}

export interface ObservationRecord {
  id: string
  tileId: string
  city: string
  chipId: string | null
  imageryVersion: string
  runId: string
  checkpointSha256: string
  predictionId: string
  decision: Decision
  species: string | null
  speciesSource: 'model' | 'reviewer' | null
  modelSpecies: string | null
  modelSpeciesConfidence: number | null
  centerConfidence: number
  predictedDbhInches: number | null
  measuredDbhInches: number | null
  predictedCrownWidthM: number | null
  crownWidthMethod: string | null
  /** Where the row publishes: the crown centre, or the linked tree's trunk. */
  latitude: number
  longitude: number
  positionRole: 'crown_center' | 'linked_trunk'
  crownCenter: { xPx: number; yPx: number; longitude: number; latitude: number }
  duplicateOfTreeId: string | null
  duplicateOf: { treeId: string; latitude: number; longitude: number; source: string; species: string | null } | null
  notes: string | null
  acquisitionDate: string
  status: 'pending' | 'published'
  revision: number
  /** Private: who made the decision on this machine.  Never exported. */
  actor: string
}

export function observationId(tileId: string, runId: string, predictionId: string): string {
  const digest = createHash('sha1').update(`${tileId}|${runId}|${predictionId}`).digest('hex')
  return `sat-${digest.slice(0, 20)}`
}

function cleanString(value: unknown, max: number, label: string): string | null {
  if (value == null || value === '') return null
  if (typeof value !== 'string') throw new Error(`${label} must be a string`)
  const trimmed = value.trim()
  if (trimmed.length > max) throw new Error(`${label} must be at most ${max} characters`)
  return trimmed || null
}

function cleanPositive(value: unknown, label: string): number | null {
  if (value == null || value === '') return null
  const n = typeof value === 'string' ? Number(value) : value
  if (!isFiniteNumber(n) || n <= 0) throw new Error(`${label} must be a positive number`)
  return n
}

export function parseObservationInput(body: unknown): ObservationInput {
  if (typeof body !== 'object' || body === null) throw new Error('Body must be a JSON object')
  const b = body as Record<string, unknown>
  if (!DECISIONS.includes(b.decision as Decision)) throw new Error(`decision must be one of ${DECISIONS.join(', ')}`)
  const input: ObservationInput = { decision: b.decision as Decision }
  input.species = cleanString(b.species, MAX_SPECIES, 'species')
  if (b.speciesSource != null) {
    if (b.speciesSource !== 'model' && b.speciesSource !== 'reviewer') throw new Error('speciesSource must be model or reviewer')
    input.speciesSource = b.speciesSource
  }
  input.measuredDbhInches = cleanPositive(b.measuredDbhInches, 'measuredDbhInches')
  input.duplicateOfTreeId = cleanString(b.duplicateOfTreeId, 200, 'duplicateOfTreeId')
  input.notes = cleanString(b.notes, MAX_NOTES, 'notes')
  if (b.crownCenter != null) {
    const c = b.crownCenter as Record<string, unknown>
    if (!isFiniteNumber(c.xPx) || !isFiniteNumber(c.yPx)) throw new Error('crownCenter needs numeric xPx and yPx')
    input.crownCenter = { xPx: c.xPx, yPx: c.yPx }
  }
  if (b.baseRevision != null) {
    if (!Number.isInteger(b.baseRevision)) throw new Error('baseRevision must be an integer')
    input.baseRevision = b.baseRevision as number
  }
  return input
}

/**
 * The record a decision becomes.  Pure: the caller supplies the bundle, the
 * prediction, the input and the actor, and gets back everything but the
 * server timestamps.
 */
export function buildObservation(
  bundle: TilePredictionBundle,
  prediction: BundlePrediction,
  input: ObservationInput,
  actor: string,
  revision: number,
): ObservationRecord {
  const { width, height } = bundle.image
  let crownPx = { xPx: prediction.xPx, yPx: prediction.yPx }
  if (input.crownCenter) {
    const { xPx, yPx } = input.crownCenter
    if (xPx < 0 || yPx < 0 || xPx > width || yPx > height) throw new Error('crownCenter is outside the tile')
    crownPx = { xPx, yPx }
  }
  const moved = crownPx.xPx !== prediction.xPx || crownPx.yPx !== prediction.yPx
  const crownGeo = moved ? pixelToLonLat(bundle, crownPx.xPx, crownPx.yPx) : { longitude: prediction.longitude, latitude: prediction.latitude }
  const crownCenter = { ...crownPx, ...crownGeo }

  let duplicateOf: ObservationRecord['duplicateOf'] = null
  if (input.decision === 'duplicate') {
    if (!input.duplicateOfTreeId) throw new Error('A duplicate decision needs duplicateOfTreeId')
    const target = bundle.inventoryTrees.find((t) => t.treeId === input.duplicateOfTreeId)
    if (!target) throw new Error(`${input.duplicateOfTreeId} is not an inventory tree on this tile`)
    duplicateOf = { treeId: target.treeId, latitude: target.latitude, longitude: target.longitude, source: target.source, species: target.species ?? null }
  } else if (input.duplicateOfTreeId) {
    throw new Error('duplicateOfTreeId only makes sense with a duplicate decision')
  }

  // Species: the reviewer either kept the model's label or typed their own.
  // A label nobody looked at is not a confirmation, so an accept with no
  // species and no source publishes as unidentified.
  let species = input.species ?? null
  let speciesSource = input.speciesSource ?? null
  if (species && !speciesSource) speciesSource = species === prediction.species ? 'model' : 'reviewer'
  if (!species) speciesSource = null
  if (speciesSource === 'model' && species !== prediction.species) speciesSource = 'reviewer'

  const position = duplicateOf
    ? { latitude: duplicateOf.latitude, longitude: duplicateOf.longitude, positionRole: 'linked_trunk' as const }
    : { latitude: crownCenter.latitude, longitude: crownCenter.longitude, positionRole: 'crown_center' as const }

  return {
    id: observationId(bundle.tileId, bundle.predictionLayer.runId, prediction.predictionId),
    tileId: bundle.tileId,
    city: bundle.city,
    chipId: bundle.chipId ?? null,
    imageryVersion: bundle.imageryVersion,
    runId: bundle.predictionLayer.runId,
    checkpointSha256: bundle.predictionLayer.checkpointSha256,
    predictionId: prediction.predictionId,
    decision: input.decision,
    species,
    speciesSource,
    modelSpecies: prediction.species ?? null,
    modelSpeciesConfidence: prediction.speciesConfidence ?? null,
    centerConfidence: prediction.centerConfidence,
    predictedDbhInches: prediction.dbhInches ?? null,
    measuredDbhInches: input.measuredDbhInches ?? null,
    predictedCrownWidthM: prediction.crownWidthM ?? null,
    crownWidthMethod: prediction.crownWidthMethod ?? null,
    ...position,
    crownCenter,
    duplicateOfTreeId: duplicateOf?.treeId ?? null,
    duplicateOf,
    notes: input.notes ?? null,
    acquisitionDate: bundle.acquisitionEnd,
    status: 'pending',
    revision,
    actor,
  }
}

// ---------------------------------------------------------------------------
// The public export
// ---------------------------------------------------------------------------

export interface ExportRow {
  schemaVersion: 1
  treeId: string
  city: string
  latitude: number
  longitude: number
  positionRole: 'crown_center' | 'linked_trunk'
  species: string | null
  speciesSource: 'model' | 'reviewer' | null
  predictedDbhInches: number | null
  measuredDbhInches: number | null
  predictedCrownWidthM: number | null
  duplicateOfTreeId: string | null
  tileId: string
  imageryVersion: string
  runId: string
  predictionId: string
  acquisitionDate: string
  publishedAt: string | null
}

/** The public shape of a published tree: no notes, no actor, no private ids. */
export function exportRow(published: Record<string, unknown>): ExportRow {
  const publishedAt = published.publishedAt instanceof Timestamp
    ? published.publishedAt.toDate().toISOString()
    : typeof published.publishedAt === 'string' ? published.publishedAt : null
  return {
    schemaVersion: EXPORT_SCHEMA_VERSION,
    treeId: String(published.treeId),
    city: String(published.city).toUpperCase(),
    latitude: published.latitude as number,
    longitude: published.longitude as number,
    positionRole: published.positionRole as ExportRow['positionRole'],
    species: (published.species as string | null) ?? null,
    speciesSource: (published.speciesSource as ExportRow['speciesSource']) ?? null,
    predictedDbhInches: (published.predictedDbhInches as number | null) ?? null,
    measuredDbhInches: (published.measuredDbhInches as number | null) ?? null,
    predictedCrownWidthM: (published.predictedCrownWidthM as number | null) ?? null,
    duplicateOfTreeId: (published.duplicateOfTreeId as string | null) ?? null,
    tileId: String(published.tileId),
    imageryVersion: String(published.imageryVersion),
    runId: String(published.runId),
    predictionId: String(published.predictionId),
    acquisitionDate: String(published.acquisitionDate),
    publishedAt,
  }
}

export interface ExportManifest {
  schemaVersion: 1
  count: number
  latestPublishedAt: string | null
  /** Per city, so a publish in one city rebuilds that city's Parquet alone. */
  latestPublishedAtByCity: Record<string, string>
}

export function buildManifest(rows: ExportRow[]): ExportManifest {
  let latestPublishedAt: string | null = null
  const latestPublishedAtByCity: Record<string, string> = {}
  for (const row of rows) {
    if (!row.publishedAt) continue
    if (!latestPublishedAt || row.publishedAt > latestPublishedAt) latestPublishedAt = row.publishedAt
    const current = latestPublishedAtByCity[row.city]
    if (!current || row.publishedAt > current) latestPublishedAtByCity[row.city] = row.publishedAt
  }
  return { schemaVersion: 1, count: rows.length, latestPublishedAt, latestPublishedAtByCity }
}

/** What a published record holds: the observation's public fields plus a tree id. */
export function publishedTreeFrom(observation: ObservationRecord): Record<string, unknown> {
  return {
    treeId: observation.id,
    observationId: observation.id,
    city: observation.city,
    latitude: observation.latitude,
    longitude: observation.longitude,
    positionRole: observation.positionRole,
    species: observation.species,
    speciesSource: observation.speciesSource,
    predictedDbhInches: observation.predictedDbhInches,
    measuredDbhInches: observation.measuredDbhInches,
    predictedCrownWidthM: observation.predictedCrownWidthM,
    duplicateOfTreeId: observation.duplicateOfTreeId,
    tileId: observation.tileId,
    imageryVersion: observation.imageryVersion,
    runId: observation.runId,
    predictionId: observation.predictionId,
    acquisitionDate: observation.acquisitionDate,
  }
}

// ---------------------------------------------------------------------------
// Tile store: bundles on disk
// ---------------------------------------------------------------------------

export interface TileSummary {
  tileId: string
  city: string
  chipId: string | null
  imageryVersion: string
  runId: string
  acquisitionStart: string
  acquisitionEnd: string
  status: 'available' | 'unavailable'
  predictionCount: number
  inventoryCount: number
}

export class TileStore {
  private cache = new Map<string, { mtimeMs: number; bundle: TilePredictionBundle }>()

  constructor(readonly dir: string) {}

  list(): TileSummary[] {
    if (!existsSync(this.dir)) return []
    return readdirSync(this.dir)
      .filter((name) => name.endsWith('.json'))
      .map((name) => this.get(name.slice(0, -'.json'.length)))
      .filter((b): b is TilePredictionBundle => b !== null)
      .map(summarize)
      .sort((x, y) => x.tileId.localeCompare(y.tileId))
  }

  get(tileId: string): TilePredictionBundle | null {
    if (!/^[A-Za-z0-9._:-]+$/.test(tileId)) return null
    const file = path.join(this.dir, `${tileId}.json`)
    if (!existsSync(file)) return null
    const mtimeMs = statSync(file).mtimeMs
    const cached = this.cache.get(tileId)
    if (cached && cached.mtimeMs === mtimeMs) return cached.bundle
    const bundle = assertBundle(JSON.parse(readFileSync(file, 'utf8')))
    if (bundle.tileId !== tileId) throw new Error(`${file} holds tile ${bundle.tileId}, not ${tileId}`)
    this.cache.set(tileId, { mtimeMs, bundle })
    return bundle
  }

  imagePath(tileId: string): string | null {
    const bundle = this.get(tileId)
    if (!bundle) return null
    const name = path.basename(bundle.image.url)
    const file = path.join(this.dir, name)
    return existsSync(file) ? file : null
  }
}

export function summarize(bundle: TilePredictionBundle): TileSummary {
  return {
    tileId: bundle.tileId,
    city: bundle.city,
    chipId: bundle.chipId ?? null,
    imageryVersion: bundle.imageryVersion,
    runId: bundle.predictionLayer.runId,
    acquisitionStart: bundle.acquisitionStart,
    acquisitionEnd: bundle.acquisitionEnd,
    status: bundle.predictionLayer.status,
    predictionCount: bundle.predictionLayer.predictions.length,
    inventoryCount: bundle.inventoryTrees.length,
  }
}

/** The bundle as the page receives it: image URL served here, inventory and
 *  observations placed in tile pixels, each prediction's nearest trees. */
export function presentBundle(bundle: TilePredictionBundle) {
  return {
    ...bundle,
    image: { ...bundle.image, url: `/api/satellite/tiles/${encodeURIComponent(bundle.tileId)}/image` },
    inventoryTrees: bundle.inventoryTrees.map((tree) => ({ ...tree, ...lonLatToPixel(bundle, tree.longitude, tree.latitude) })),
    predictionLayer: {
      ...bundle.predictionLayer,
      predictions: bundle.predictionLayer.predictions.map((p) => ({
        ...p,
        nearest: nearestInventory(bundle, p.longitude, p.latitude),
      })),
    },
  }
}

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export interface PublishedBucket {
  file(path: string): { save(data: string, options: { contentType: string; metadata: { cacheControl: string } }): Promise<unknown> }
}

export interface SatelliteDeps {
  db: Firestore
  publishedBucket: PublishedBucket
  cityCodes: Set<string>
  tiles: TileStore
  actor?: string
}

function toIso(value: unknown): string | null {
  return value instanceof Timestamp ? value.toDate().toISOString() : null
}

function serializeObservation(doc: FirebaseFirestore.DocumentSnapshot): Record<string, unknown> {
  const data = doc.data() ?? {}
  return { ...data, id: doc.id, createdAt: toIso(data.createdAt), updatedAt: toIso(data.updatedAt), publishedAt: toIso(data.publishedAt) }
}

export function createSatelliteRouter(deps: SatelliteDeps): express.Router {
  const { db, publishedBucket, cityCodes, tiles } = deps
  const actor = deps.actor ?? os.userInfo().username
  const router = express.Router()

  async function observationsForTile(tileId: string) {
    const snapshot = await db.collection(OBSERVATIONS).where('tileId', '==', tileId).get()
    return snapshot.docs.map(serializeObservation)
  }

  /** Rewrite the public export from every published satellite tree. */
  async function rewritePublicExport(): Promise<ExportManifest> {
    const snapshot = await db.collection(PUBLISHED).orderBy('publishedAt', 'asc').get()
    const rows = snapshot.docs.map((doc) => exportRow(doc.data()))
    const manifest = buildManifest(rows)
    // No cache: the freshness probe must observe a publish on the next run.
    const writeOptions = { contentType: 'application/json', metadata: { cacheControl: 'no-cache' } }
    await publishedBucket.file(SATELLITE_EXPORT_PATH).save(`${rows.map((r) => JSON.stringify(r)).join('\n')}\n`, writeOptions)
    await publishedBucket.file(SATELLITE_MANIFEST_PATH).save(JSON.stringify(manifest), writeOptions)
    return manifest
  }

  /** Publish one pending observation: create the public record, mark the observation. */
  async function publishObservation(observationId: string): Promise<string> {
    const observationRef = db.collection(OBSERVATIONS).doc(observationId)
    const publishedRef = db.collection(PUBLISHED).doc(observationId)
    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(observationRef)
      if (!snapshot.exists) throw new Error(`Observation ${observationId} not found`)
      const observation = snapshot.data() as ObservationRecord
      if (observation.status !== 'pending') throw new Error(`Observation ${observationId} is already ${observation.status}`)
      if (!PUBLISHABLE.has(observation.decision)) throw new Error(`A ${observation.decision} decision is not published`)
      if (!cityCodes.has(observation.city)) throw new Error(`${observation.city} is not a supported city code`)
      transaction.create(publishedRef, { ...publishedTreeFrom(observation), publishedAt: FieldValue.serverTimestamp() })
      transaction.update(observationRef, { status: 'published', publishedAt: FieldValue.serverTimestamp(), publishedTreeId: publishedRef.id })
    })
    return publishedRef.id
  }

  router.get('/tiles', async (_req, res, next) => {
    try {
      const summaries = tiles.list()
      const rows = await Promise.all(summaries.map(async (summary) => {
        const [observations, review] = await Promise.all([
          observationsForTile(summary.tileId),
          db.collection(TILE_REVIEWS).doc(summary.tileId).get(),
        ])
        const counts: Record<string, number> = { accept: 0, duplicate: 0, reject: 0, uncertain: 0, published: 0 }
        for (const o of observations) {
          counts[o.decision as string] = (counts[o.decision as string] ?? 0) + 1
          if (o.status === 'published') counts.published += 1
        }
        const reviewData = review.data()
        return { ...summary, counts, done: Boolean(reviewData?.done), doneAt: toIso(reviewData?.doneAt) }
      }))
      res.json(rows)
    } catch (error) {
      next(error)
    }
  })

  router.get('/tiles/:tileId', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId)
      if (!bundle) {
        res.status(404).json({ error: `No tile ${req.params.tileId}` })
        return
      }
      const [observations, review, nearby] = await Promise.all([
        observationsForTile(bundle.tileId),
        db.collection(TILE_REVIEWS).doc(bundle.tileId).get(),
        // Decisions from other tiles that fall on this one (tiles overlap
        // at their edges, and an imagery re-run makes a new tile id): the
        // page draws them so a tree accepted once is not accepted twice.
        db.collection(OBSERVATIONS).where('city', '==', bundle.city).get(),
      ])
      const bounds = bundle.image.bounds
      const nearbyObservations = nearby.docs
        .map(serializeObservation)
        .filter((o) => o.tileId !== bundle.tileId && PUBLISHABLE.has(o.decision as Decision))
        .filter((o) => !bounds || (
          (o.latitude as number) >= bounds.south && (o.latitude as number) <= bounds.north &&
          (o.longitude as number) >= bounds.west && (o.longitude as number) <= bounds.east
        ))
        .map((o) => ({ ...o, ...lonLatToPixel(bundle, o.longitude as number, o.latitude as number) }))
      const reviewData = review.data()
      res.json({
        bundle: presentBundle(bundle),
        observations,
        nearbyObservations,
        review: { done: Boolean(reviewData?.done), doneAt: toIso(reviewData?.doneAt), imageryVersion: reviewData?.imageryVersion ?? null, runId: reviewData?.runId ?? null },
      })
    } catch (error) {
      next(error)
    }
  })

  router.get('/tiles/:tileId/image', (req, res) => {
    const file = tiles.imagePath(req.params.tileId)
    if (!file) {
      res.status(404).json({ error: 'No image for that tile' })
      return
    }
    res.type('image/png').sendFile(file)
  })

  router.put('/tiles/:tileId/observations/:predictionId', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId)
      if (!bundle) throw new Error(`No tile ${req.params.tileId}`)
      const prediction = bundle.predictionLayer.predictions.find((p) => p.predictionId === req.params.predictionId)
      if (!prediction) throw new Error(`No prediction ${req.params.predictionId} on ${bundle.tileId}`)
      const input = parseObservationInput(req.body)
      const ref = db.collection(OBSERVATIONS).doc(observationId(bundle.tileId, bundle.predictionLayer.runId, prediction.predictionId))
      const result = await db.runTransaction(async (transaction) => {
        const existing = await transaction.get(ref)
        const current = existing.exists ? (existing.data() as ObservationRecord) : null
        if (current?.status === 'published') {
          const error = new Error(`${ref.id} is published; withdraw it from the queue before changing it`)
          ;(error as Error & { status?: number }).status = 409
          throw error
        }
        const currentRevision = current?.revision ?? 0
        if (input.baseRevision != null && input.baseRevision !== currentRevision) {
          const error = new Error(`Stale edit: ${ref.id} is at revision ${currentRevision}, you saw ${input.baseRevision}`)
          ;(error as Error & { status?: number }).status = 409
          throw error
        }
        const record = buildObservation(bundle, prediction, input, actor, currentRevision + 1)
        transaction.set(ref, {
          ...record,
          createdAt: current ? (existing.data()?.createdAt ?? FieldValue.serverTimestamp()) : FieldValue.serverTimestamp(),
          updatedAt: FieldValue.serverTimestamp(),
        })
        return record
      })
      res.json({ ok: true, observation: result })
    } catch (error) {
      next(error)
    }
  })

  router.delete('/tiles/:tileId/observations/:predictionId', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId)
      if (!bundle) throw new Error(`No tile ${req.params.tileId}`)
      const ref = db.collection(OBSERVATIONS).doc(observationId(bundle.tileId, bundle.predictionLayer.runId, req.params.predictionId))
      await db.runTransaction(async (transaction) => {
        const existing = await transaction.get(ref)
        if (!existing.exists) return
        if (existing.data()?.status === 'published') throw new Error(`${ref.id} is published and cannot be cleared`)
        transaction.delete(ref)
      })
      res.json({ ok: true })
    } catch (error) {
      next(error)
    }
  })

  router.post('/tiles/:tileId/done', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId)
      if (!bundle) throw new Error(`No tile ${req.params.tileId}`)
      const done = Boolean((req.body as { done?: unknown })?.done)
      // Keyed by imagery version and run: "done" means this image, with this
      // prediction layer, was looked at -- not that the trees are settled.
      await db.collection(TILE_REVIEWS).doc(bundle.tileId).set({
        tileId: bundle.tileId,
        city: bundle.city,
        imageryVersion: bundle.imageryVersion,
        runId: bundle.predictionLayer.runId,
        done,
        doneAt: done ? FieldValue.serverTimestamp() : null,
        actor,
      })
      res.json({ ok: true, done })
    } catch (error) {
      next(error)
    }
  })

  router.get('/queue', async (_req, res, next) => {
    try {
      const snapshot = await db.collection(OBSERVATIONS).where('status', '==', 'pending').get()
      const rows = snapshot.docs.map(serializeObservation).filter((o) => PUBLISHABLE.has(o.decision as Decision))
      rows.sort((x, y) => String(x.updatedAt ?? '').localeCompare(String(y.updatedAt ?? '')))
      res.json(rows)
    } catch (error) {
      next(error)
    }
  })

  router.post('/observations/:id/publish', async (req, res, next) => {
    try {
      const publishedTreeId = await publishObservation(req.params.id)
      const manifest = await rewritePublicExport()
      res.json({ ok: true, publishedTreeId, published: manifest.count })
    } catch (error) {
      next(error)
    }
  })

  router.post('/tiles/:tileId/publish', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId)
      if (!bundle) throw new Error(`No tile ${req.params.tileId}`)
      const pending = (await observationsForTile(bundle.tileId))
        .filter((o) => o.status === 'pending' && PUBLISHABLE.has(o.decision as Decision))
      const published: string[] = []
      const failed: { id: string; error: string }[] = []
      for (const observation of pending) {
        try {
          published.push(await publishObservation(observation.id as string))
        } catch (error) {
          failed.push({ id: observation.id as string, error: (error as Error).message })
        }
      }
      // One export rewrite for the batch; a partial failure still exports
      // what did publish, and reports the rest.
      const manifest = published.length ? await rewritePublicExport() : null
      res.json({ ok: failed.length === 0, published, failed, exported: manifest?.count ?? null })
    } catch (error) {
      next(error)
    }
  })

  // Rebuild the export from Firestore without publishing anything -- the first
  // run, or recovery if a publish committed but the export write failed.
  router.post('/republish', async (_req, res, next) => {
    try {
      res.json({ ok: true, manifest: await rewritePublicExport() })
    } catch (error) {
      next(error)
    }
  })

  return router
}
