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
 * and read from `SATELLITE_TILE_DIR` (default: reviewer/fixtures/tiles) and
 * every directory below it, one tile set per subdirectory.
 * The contract is `TilePredictionBundle` below; `assertBundle` refuses what
 * the exporter's `validate_bundle` refuses, and the two are kept in step.
 *
 * Three rules the storage encodes, from docs/PREDICTION_CURATION_HANDOFF.md:
 *
 *  - A rejection or an "uncertain" is a statement about this image, never
 *    about the inventory: those decisions are stored and never published,
 *    and no decision can delete or edit a canonical tree.
 *  - A model estimate is never promoted to a measurement.  The export carries
 *    `predictedDbhInches` and `measuredDbhInches` separately; the ingest
 *    (`data/raw/satellite_tree_info.py`) publishes only the measured one.
 *  - A satellite tree is corrected in place, never stacked.  Linking a
 *    detection to an already-published satellite tree publishes nothing (two
 *    satellite rows in one cluster merge field by field with max(), so a
 *    second row is not a reliable correction and can split the position);
 *    the reviewer edits that tree's record instead, and a stacked row can be
 *    folded into its target and withdrawn.
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
import sharp from 'sharp'
import { addFreezeRoutes } from './frozenPredictions.ts'

export const BUNDLE_SCHEMA_VERSION = 1
export const EXPORT_SCHEMA_VERSION = 1
export const SATELLITE_EXPORT_PATH = 'satellite/published_trees.ndjson'
export const SATELLITE_MANIFEST_PATH = 'satellite/manifest.json'
export const OBSERVATIONS = 'satelliteObservations'
export const PUBLISHED = 'satelliteTrees'
export const TILE_REVIEWS = 'satelliteTileReviews'
const NEAREST_LIMIT = 6
/** Inventory trees farther than this from a detection are not offered as its duplicate. */
export const DUPLICATE_RADIUS_M = 5
const MAX_NOTES = 500
const MAX_SPECIES = 120
/** A reviewer-traced crown wider than this is a slip of the mouse, not a tree. */
export const MAX_HUMAN_CROWN_M = 50
const EARTH_RADIUS_M = 6_371_008.8
/** Longest side of the map overlay's tile thumbnails, in pixels. */
const THUMB_PX = 256

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
  /** The model's own crown-width head, when the run has one. */
  modelCrownWidthM?: number | null
  modelCrownWidthMethod?: string | null
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

/**
 * The tile's corners as [longitude, latitude]: top-left, top-right,
 * bottom-right, bottom-left, the order a MapLibre image source takes.  The
 * tile is square in its own CRS (UTM), so on the map it is slightly rotated.
 */
export function tileFootprint(bundle: TilePredictionBundle): [number, number][] {
  const { width, height } = bundle.image
  return ([[0, 0], [width, 0], [width, height], [0, height]] as const).map(([x, y]) => {
    const { longitude, latitude } = pixelToLonLat(bundle, x, y)
    return [longitude, latitude]
  })
}

/** The footprint's lon/lat envelope. */
export function tileEnvelope(bundle: TilePredictionBundle): { west: number; east: number; south: number; north: number } {
  const corners = tileFootprint(bundle)
  const lons = corners.map((c) => c[0])
  const lats = corners.map((c) => c[1])
  return { west: Math.min(...lons), east: Math.max(...lons), south: Math.min(...lats), north: Math.max(...lats) }
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

/** Inventory trees within maxDistanceM of a point, nearest first.  Suggestions, never a merge. */
export function nearestInventory(
  bundle: TilePredictionBundle,
  longitude: number,
  latitude: number,
  limit = NEAREST_LIMIT,
  maxDistanceM = DUPLICATE_RADIUS_M,
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
    .filter((tree) => tree.distanceM <= maxDistanceM)
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

/** A tree this reviewer published: its id is the observation's (see observationId). */
export function isSatelliteTreeId(treeId: unknown): boolean {
  return typeof treeId === 'string' && treeId.startsWith('sat-')
}

/** Accepts and duplicates publish -- except a duplicate of a satellite tree, which is corrected instead. */
export function isPublishable(o: { decision?: unknown; duplicateOfTreeId?: unknown }): boolean {
  return PUBLISHABLE.has(o.decision as Decision) && !(o.decision === 'duplicate' && isSatelliteTreeId(o.duplicateOfTreeId))
}

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
  /** Crown width the reviewer traced on the image, metres; null when not traced. */
  humanCrownWidthM?: number | null
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
  /** The model's crown head when the run has one, else the Tallo estimate from DBH. */
  predictedCrownWidthM: number | null
  crownWidthMethod: string | null
  /** The Tallo estimate from the model's DBH, kept for comparison. */
  allometricCrownWidthM: number | null
  /** Traced by the reviewer on the image; when set, this is the crown that publishes. */
  humanCrownWidthM: number | null
  /** Where the row publishes: the crown centre, or the linked tree's trunk. */
  latitude: number
  longitude: number
  positionRole: 'crown_center' | 'linked_trunk'
  crownCenter: { xPx: number; yPx: number; longitude: number; latitude: number }
  duplicateOfTreeId: string | null
  duplicateOf: { treeId: string; latitude: number; longitude: number; source: string; species: string | null } | null
  notes: string | null
  acquisitionDate: string
  status: 'pending' | 'published' | 'withdrawn'
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
  input.humanCrownWidthM = cleanPositive(b.humanCrownWidthM, 'humanCrownWidthM')
  if (input.humanCrownWidthM != null && input.humanCrownWidthM > MAX_HUMAN_CROWN_M) {
    throw new Error(`humanCrownWidthM must be at most ${MAX_HUMAN_CROWN_M} m`)
  }
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
    predictedCrownWidthM: prediction.modelCrownWidthM ?? prediction.crownWidthM ?? null,
    crownWidthMethod: prediction.modelCrownWidthM != null
      ? prediction.modelCrownWidthMethod ?? 'model_crown_head'
      : prediction.crownWidthMethod ?? null,
    allometricCrownWidthM: prediction.crownWidthM ?? null,
    humanCrownWidthM: input.humanCrownWidthM ?? null,
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
  /** model_crown_head is read from the image; tallo_* is allometric from the model's DBH. */
  crownWidthMethod: string | null
  /** Traced by the reviewer on the image; the ingest prefers it to the model's. */
  humanCrownWidthM: number | null
  duplicateOfTreeId: string | null
  /** Null on a frozen prediction, which no reviewed tile produced. */
  tileId: string | null
  imageryVersion: string | null
  runId: string | null
  predictionId: string | null
  acquisitionDate: string | null
  /** `bulk-confirmed` for a bulk prediction a person confirmed (frozenPredictions.ts); null for a reviewed detection. */
  origin: string | null
  publishedAt: string | null
  /** The last in-place correction of this tree, if any. */
  correctedAt: string | null
}

function isoOrNull(value: unknown): string | null {
  return value instanceof Timestamp ? value.toDate().toISOString() : typeof value === 'string' ? value : null
}

function stringOrNull(value: unknown): string | null {
  return value === null || value === undefined ? null : String(value)
}

/** The public shape of a published tree: no notes, no actor, no private ids. */
export function exportRow(published: Record<string, unknown>): ExportRow {
  const publishedAt = isoOrNull(published.publishedAt)
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
    crownWidthMethod: (published.crownWidthMethod as string | null) ?? null,
    humanCrownWidthM: (published.humanCrownWidthM as number | null) ?? null,
    duplicateOfTreeId: (published.duplicateOfTreeId as string | null) ?? null,
    tileId: stringOrNull(published.tileId),
    imageryVersion: stringOrNull(published.imageryVersion),
    runId: stringOrNull(published.runId),
    predictionId: stringOrNull(published.predictionId),
    acquisitionDate: stringOrNull(published.acquisitionDate),
    origin: stringOrNull(published.origin),
    publishedAt,
    correctedAt: isoOrNull(published.correctedAt),
  }
}

export interface ExportManifest {
  schemaVersion: 1
  count: number
  latestPublishedAt: string | null
  /** Per city, so a publish in one city rebuilds that city's Parquet alone. */
  latestPublishedAtByCity: Record<string, string>
}

/**
 * The freshness the city refresh probes.  "Published" means the city's export
 * last changed: a publish, an in-place correction, or a withdrawal (whose row
 * is gone, so it is passed separately) all move it forward.
 */
export function buildManifest(rows: ExportRow[], withdrawals: { city: string; at: string | null }[] = []): ExportManifest {
  let latestPublishedAt: string | null = null
  const latestPublishedAtByCity: Record<string, string> = {}
  const changes = [
    ...rows.map((row) => ({ city: row.city, at: [row.publishedAt, row.correctedAt].filter(Boolean).sort().pop() ?? null })),
    ...withdrawals.map((w) => ({ city: w.city.toUpperCase(), at: w.at })),
  ]
  for (const { city, at } of changes) {
    if (!at) continue
    if (!latestPublishedAt || at > latestPublishedAt) latestPublishedAt = at
    const current = latestPublishedAtByCity[city]
    if (!current || at > current) latestPublishedAtByCity[city] = at
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
    crownWidthMethod: observation.crownWidthMethod,
    humanCrownWidthM: observation.humanCrownWidthM ?? null,
    duplicateOfTreeId: observation.duplicateOfTreeId,
    tileId: observation.tileId,
    imageryVersion: observation.imageryVersion,
    runId: observation.runId,
    predictionId: observation.predictionId,
    acquisitionDate: observation.acquisitionDate,
  }
}

// ---------------------------------------------------------------------------
// Correcting a published satellite tree in place
// ---------------------------------------------------------------------------

/** A correction moving a tree farther than this is a mis-click, not a nudge. */
export const MAX_CORRECTION_MOVE_M = 25
const MAX_DBH_INCHES = 200
const MAX_CORRECTIONS_KEPT = 20
const CORRECTABLE = ['species', 'measuredDbhInches', 'humanCrownWidthM', 'latitude', 'longitude'] as const
type Correctable = (typeof CORRECTABLE)[number]

export interface CorrectionInput {
  /** Only the fields present are changed; null clears one. */
  changes: Partial<Record<Correctable, string | number | null>>
  baseRevision: number | null
}

export function parseCorrection(body: unknown): CorrectionInput {
  if (typeof body !== 'object' || body === null) throw new Error('Body must be a JSON object')
  const b = body as Record<string, unknown>
  const changes: CorrectionInput['changes'] = {}
  if ('species' in b) changes.species = cleanString(b.species, MAX_SPECIES, 'species')
  if ('measuredDbhInches' in b) {
    const dbh = cleanPositive(b.measuredDbhInches, 'measuredDbhInches')
    if (dbh != null && dbh > MAX_DBH_INCHES) throw new Error(`measuredDbhInches must be at most ${MAX_DBH_INCHES}`)
    changes.measuredDbhInches = dbh
  }
  if ('humanCrownWidthM' in b) {
    const crown = cleanPositive(b.humanCrownWidthM, 'humanCrownWidthM')
    if (crown != null && crown > MAX_HUMAN_CROWN_M) throw new Error(`humanCrownWidthM must be at most ${MAX_HUMAN_CROWN_M} m`)
    changes.humanCrownWidthM = crown
  }
  if ('latitude' in b || 'longitude' in b) {
    const lat = Number(b.latitude), lon = Number(b.longitude)
    if (!isFiniteNumber(lat) || !isFiniteNumber(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) throw new Error('a position needs both latitude and longitude')
    changes.latitude = lat
    changes.longitude = lon
  }
  if (!Object.keys(changes).length) throw new Error('Nothing to correct')
  const base = b.baseRevision
  if (base != null && (!Number.isInteger(base) || (base as number) < 0)) throw new Error('baseRevision must be a non-negative integer')
  return { changes, baseRevision: (base as number | null) ?? null }
}

/**
 * The update a correction makes to a published record: the changed fields,
 * a bumped revision, correctedAt, and a short history of what each field was.
 * A linked-trunk row sits on another tree's trunk by design, so it never moves.
 */
export function correctionUpdate(current: Record<string, unknown>, input: CorrectionInput, actor: string, now: string): Record<string, unknown> {
  if (current.withdrawnAt) throw new Error(`${String(current.treeId)} is withdrawn`)
  const revision = (current.revision as number | undefined) ?? 0
  if (input.baseRevision != null && input.baseRevision !== revision) {
    const error = new Error(`Stale correction: ${String(current.treeId)} is at revision ${revision}, you saw ${input.baseRevision}`)
    ;(error as Error & { status?: number }).status = 409
    throw error
  }
  const changes = { ...input.changes }
  if ('latitude' in changes) {
    if (current.positionRole !== 'crown_center') throw new Error('A linked-trunk tree stays on the trunk it was linked to; its position is not corrected')
    const moved = haversineM(current.longitude as number, current.latitude as number, changes.longitude as number, changes.latitude as number)
    if (moved > MAX_CORRECTION_MOVE_M) throw new Error(`That moves the tree ${moved.toFixed(0)} m; a correction moves it at most ${MAX_CORRECTION_MOVE_M} m`)
  }
  const diff: Record<string, { from: unknown; to: unknown }> = {}
  for (const key of CORRECTABLE) {
    if (!(key in changes)) continue
    const from = current[key] ?? null
    if (from !== changes[key]) diff[key] = { from, to: changes[key] }
  }
  if (!Object.keys(diff).length) throw new Error('That is what the tree already holds')
  const update: Record<string, unknown> = Object.fromEntries(Object.entries(diff).map(([k, v]) => [k, v.to]))
  if ('species' in diff) update.speciesSource = diff.species.to ? 'reviewer' : null
  const history = Array.isArray(current.corrections) ? current.corrections : []
  update.corrections = [...history, { at: now, actor, changes: diff }].slice(-MAX_CORRECTIONS_KEPT)
  update.revision = revision + 1
  return update
}

/**
 * What a stacked row (a duplicate of a satellite tree, published before that
 * was refused) can give its target: the values a person entered -- a typed
 * species, a measured DBH, a traced crown.  The model's own estimates stay.
 */
export function foldChanges(stacked: Record<string, unknown>, target: Record<string, unknown>): CorrectionInput['changes'] {
  const changes: CorrectionInput['changes'] = {}
  if (stacked.speciesSource === 'reviewer' && stacked.species && stacked.species !== target.species) changes.species = stacked.species as string
  if (stacked.measuredDbhInches != null && stacked.measuredDbhInches !== target.measuredDbhInches) changes.measuredDbhInches = stacked.measuredDbhInches as number
  if (stacked.humanCrownWidthM != null && stacked.humanCrownWidthM !== target.humanCrownWidthM) changes.humanCrownWidthM = stacked.humanCrownWidthM as number
  return changes
}

/**
 * Why one published satellite tree cannot be linked as a duplicate of
 * another, or null when it can.  Two accepts of one tree (another run, an
 * overlapping tile) publish as separate rows that the shared merge never
 * joins; the link is what puts the later one in the stacked list to fold.
 */
export function linkProblem(source: Record<string, unknown> | null, target: Record<string, unknown> | null, sourceId: string, targetId: string): string | null {
  if (!isSatelliteTreeId(sourceId) || !isSatelliteTreeId(targetId)) return 'Only satellite trees (sat-…) link to each other'
  if (sourceId === targetId) return 'A tree cannot duplicate itself'
  if (!source) return `No published satellite tree ${sourceId}`
  if (!target) return `No published satellite tree ${targetId}`
  if (source.withdrawnAt) return `${sourceId} is withdrawn`
  if (target.withdrawnAt) return `${targetId} is withdrawn`
  if (target.duplicateOfTreeId === sourceId) return `${targetId} is already linked to ${sourceId}`
  if (String(source.city).toUpperCase() !== String(target.city).toUpperCase()) return 'The two trees are in different cities'
  const d = haversineM(source.longitude as number, source.latitude as number, target.longitude as number, target.latitude as number)
  if (!(d <= DUPLICATE_RADIUS_M)) return `The trees are ${d.toFixed(1)} m apart; a link needs ${DUPLICATE_RADIUS_M} m or less`
  return null
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
  /** The tile set: the subdirectory of the tile directory it was read from, '' at the top. */
  collection: string
  /** Corners on the map, see tileFootprint. */
  footprint: [number, number][]
}

export interface CitySummary {
  city: string
  tileCount: number
  collections: string[]
}

/**
 * The bundles under a tile directory, at any depth, so one reviewer serves
 * every exported set.  A tile id names a place, imagery and chip, not an
 * export, so the same tile can sit in several sets -- one per model run
 * exported over the same chips.  Every read names the run it wants (the
 * page carries it in its URL): that run's bundle wins when one holds the
 * tile, otherwise the newest file does, as a re-export would.  A read that
 * names no run uses `defaultRun`.
 */
export class TileStore {
  private cache = new Map<string, { mtimeMs: number; bundle: TilePredictionBundle }>()
  private files = new Map<string, Map<string, string>>()
  private thumbs = new Map<string, { mtimeMs: number; data: Buffer }>()
  readonly defaultRun: string | null

  constructor(readonly dir: string, options: { defaultRun?: string | null } = {}) {
    this.defaultRun = options.defaultRun ?? null
  }

  private runFor(run: string | null | undefined): string | null {
    return run === undefined ? this.defaultRun : run
  }

  private read(file: string): TilePredictionBundle {
    const mtimeMs = statSync(file).mtimeMs
    const cached = this.cache.get(file)
    if (cached && cached.mtimeMs === mtimeMs) return cached.bundle
    const bundle = assertBundle(JSON.parse(readFileSync(file, 'utf8')))
    this.cache.set(file, { mtimeMs, bundle })
    return bundle
  }

  private runOf(file: string): string | null {
    try {
      return this.read(file).predictionLayer.runId
    } catch {
      return null
    }
  }

  /** Every bundle file, by tile id, newest first. */
  private candidates(): Map<string, string[]> {
    const found = new Map<string, { file: string; mtimeMs: number }[]>()
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
          walk(full)
        } else if (entry.name.endsWith('.json')) {
          const tileId = entry.name.slice(0, -'.json'.length)
          const list = found.get(tileId) ?? []
          list.push({ file: full, mtimeMs: statSync(full).mtimeMs })
          found.set(tileId, list)
        }
      }
    }
    if (existsSync(this.dir)) walk(this.dir)
    return new Map([...found].map(([tileId, list]) => [tileId, list.sort((a, b) => b.mtimeMs - a.mtimeMs).map((c) => c.file)]))
  }

  private index(run: string | null): Map<string, string> {
    const chosen = new Map<string, string>()
    for (const [tileId, files] of this.candidates()) {
      const preferred = run && files.length > 1 ? files.find((f) => this.runOf(f) === run) : undefined
      chosen.set(tileId, preferred ?? files[0])
    }
    this.files.set(run ?? '', chosen)
    return chosen
  }

  private file(tileId: string, run: string | null): string | null {
    const known = this.files.get(run ?? '')?.get(tileId)
    if (known && existsSync(known)) return known
    return this.index(run).get(tileId) ?? null
  }

  private collection(file: string): string {
    return path.relative(this.dir, path.dirname(file)).split(path.sep).join('/')
  }

  /** Every tile, or one city's (case-insensitive), grouped by tile set. */
  list(city?: string, run?: string | null): TileSummary[] {
    const wanted = city?.toUpperCase()
    const r = this.runFor(run)
    return [...this.index(r)]
      .map(([tileId, file]) => ({ bundle: this.get(tileId, r), file }))
      .filter((t): t is { bundle: TilePredictionBundle; file: string } =>
        t.bundle !== null && (!wanted || t.bundle.city.toUpperCase() === wanted))
      .map(({ bundle, file }) => summarize(bundle, this.collection(file)))
      .sort((x, y) => x.collection.localeCompare(y.collection) || x.tileId.localeCompare(y.tileId))
  }

  /** How many tiles each run has bundles for, shadowed ones included. */
  runCounts(city?: string): Record<string, number> {
    const wanted = city?.toUpperCase()
    const counts: Record<string, number> = {}
    for (const files of this.candidates().values()) {
      const runs = new Set<string>()
      for (const file of files) {
        try {
          const bundle = this.read(file)
          if (!wanted || bundle.city.toUpperCase() === wanted) runs.add(bundle.predictionLayer.runId)
        } catch {
          // an unreadable bundle is reported when the tile is opened
        }
      }
      for (const run of runs) counts[run] = (counts[run] ?? 0) + 1
    }
    return counts
  }

  /** The chips one run already has a bundle for in a city, in any set. */
  chipIds(city: string, runId: string): Set<string> {
    const wanted = city.toUpperCase()
    const chips = new Set<string>()
    for (const files of this.candidates().values()) {
      for (const file of files) {
        try {
          const bundle = this.read(file)
          if (bundle.chipId && bundle.city.toUpperCase() === wanted && bundle.predictionLayer.runId === runId) chips.add(bundle.chipId)
        } catch {
          // an unreadable bundle is reported when the tile is opened
        }
      }
    }
    return chips
  }

  cities(run?: string | null): CitySummary[] {
    const byCity = new Map<string, CitySummary>()
    for (const tile of this.list(undefined, run)) {
      const entry = byCity.get(tile.city) ?? { city: tile.city, tileCount: 0, collections: [] }
      entry.tileCount += 1
      if (!entry.collections.includes(tile.collection)) entry.collections.push(tile.collection)
      byCity.set(tile.city, entry)
    }
    return [...byCity.values()].sort((x, y) => x.city.localeCompare(y.city))
  }

  get(tileId: string, run?: string | null): TilePredictionBundle | null {
    if (!/^[A-Za-z0-9._:-]+$/.test(tileId)) return null
    const file = this.file(tileId, this.runFor(run))
    if (!file) return null
    const bundle = this.read(file)
    if (bundle.tileId !== tileId) throw new Error(`${file} holds tile ${bundle.tileId}, not ${tileId}`)
    return bundle
  }

  /** A small JPEG of the tile for the map overlay, re-made when the PNG changes. */
  async thumbnail(tileId: string, run?: string | null): Promise<Buffer | null> {
    const file = this.imagePath(tileId, run)
    if (!file) return null
    const mtimeMs = statSync(file).mtimeMs
    const cached = this.thumbs.get(file)
    if (cached && cached.mtimeMs === mtimeMs) return cached.data
    const data = await sharp(file).resize(THUMB_PX, THUMB_PX, { fit: 'inside' }).removeAlpha().jpeg({ quality: 80 }).toBuffer()
    this.thumbs.set(file, { mtimeMs, data })
    return data
  }

  imagePath(tileId: string, run?: string | null): string | null {
    const r = this.runFor(run)
    const bundle = this.get(tileId, r)
    const json = bundle && this.file(tileId, r)
    if (!bundle || !json) return null
    // The PNG sits beside its bundle, in whichever set that is.
    const file = path.join(path.dirname(json), path.basename(bundle.image.url))
    return existsSync(file) ? file : null
  }
}

export function summarize(bundle: TilePredictionBundle, collection = ''): TileSummary {
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
    collection,
    footprint: tileFootprint(bundle),
  }
}

/** The bundle as the page receives it: image URL served here, inventory and
 *  observations placed in tile pixels, each prediction's nearest trees. */
export function presentBundle(bundle: TilePredictionBundle) {
  const run = encodeURIComponent(bundle.predictionLayer.runId)
  return {
    ...bundle,
    image: { ...bundle.image, url: `/api/satellite/tiles/${encodeURIComponent(bundle.tileId)}/image?run=${run}` },
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
  /** The run the page is reviewing (`?run=`); absent, the store's default. */
  const runOf = (req: express.Request): string | undefined =>
    typeof req.query.run === 'string' && req.query.run ? req.query.run : undefined

  async function observationsForTile(tileId: string) {
    const snapshot = await db.collection(OBSERVATIONS).where('tileId', '==', tileId).get()
    return snapshot.docs.map(serializeObservation)
  }

  /** Rewrite the public export from every published satellite tree. */
  // Rewrites run one at a time: two folds a moment apart each read Firestore
  // and then write, and an older read finishing last would bring a withdrawn
  // tree back into the export.  Each rewrite starts after the one before it
  // ends, so it reads every change committed before its caller asked.
  let exportChain: Promise<unknown> = Promise.resolve()
  function rewritePublicExport(): Promise<ExportManifest> {
    const run = exportChain.then(writePublicExport, writePublicExport)
    exportChain = run.catch(() => {})
    return run
  }

  async function writePublicExport(): Promise<ExportManifest> {
    const snapshot = await db.collection(PUBLISHED).orderBy('publishedAt', 'asc').get()
    const live = snapshot.docs.filter((doc) => !doc.data().withdrawnAt)
    const rows = live.map((doc) => exportRow(doc.data()))
    const withdrawals = snapshot.docs.filter((doc) => doc.data().withdrawnAt)
      .map((doc) => ({ city: String(doc.data().city), at: isoOrNull(doc.data().withdrawnAt) }))
    const manifest = buildManifest(rows, withdrawals)
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
      if (!isPublishable(observation)) throw new Error(`${observation.duplicateOfTreeId} is a satellite tree: correct it instead of stacking a second row on it`)
      if (!cityCodes.has(observation.city)) throw new Error(`${observation.city} is not a supported city code`)
      transaction.create(publishedRef, { ...publishedTreeFrom(observation), publishedAt: FieldValue.serverTimestamp() })
      transaction.update(observationRef, { status: 'published', publishedAt: FieldValue.serverTimestamp(), publishedTreeId: publishedRef.id })
    })
    return publishedRef.id
  }

  router.get('/cities', (req, res, next) => {
    try {
      res.json(tiles.cities(runOf(req)))
    } catch (error) {
      next(error)
    }
  })

  // `?city=` narrows the listing to one city: each row costs two Firestore
  // reads, and the tile directory holds hundreds of tiles across its sets.
  router.get('/tiles', async (req, res, next) => {
    try {
      const city = typeof req.query.city === 'string' ? req.query.city.toUpperCase() : undefined
      const summaries = tiles.list(city, runOf(req))
      if (!summaries.length) {
        res.json([])
        return
      }
      // Two reads for the whole listing rather than two per tile: the city's
      // decisions in one query, and every tile's review doc in one batch.
      const observationQuery = city
        ? db.collection(OBSERVATIONS).where('city', '==', city)
        : db.collection(OBSERVATIONS)
      const [observationSnapshot, reviews] = await Promise.all([
        observationQuery.get(),
        db.getAll(...summaries.map((s) => db.collection(TILE_REVIEWS).doc(s.tileId))),
      ])
      const byTile = new Map<string, Record<string, unknown>[]>()
      for (const doc of observationSnapshot.docs) {
        const o = doc.data()
        const list = byTile.get(o.tileId as string) ?? []
        list.push(o)
        byTile.set(o.tileId as string, list)
      }
      const rows = summaries.map((summary, i) => {
        const counts: Record<string, number> = { accept: 0, duplicate: 0, reject: 0, uncertain: 0, published: 0 }
        for (const o of byTile.get(summary.tileId) ?? []) {
          if (o.runId !== summary.runId) continue
          counts[o.decision as string] = (counts[o.decision as string] ?? 0) + 1
          if (o.status === 'published') counts.published += 1
        }
        const reviewData = reviews[i].data()
        // Done is per prediction layer: another run's review of this tile is not this one's.
        const done = Boolean(reviewData?.done) && reviewData?.runId === summary.runId
        return { ...summary, counts, done, doneAt: done ? toIso(reviewData?.doneAt) : null }
      })
      res.json(rows)
    } catch (error) {
      next(error)
    }
  })

  router.get('/tiles/:tileId', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId, runOf(req))
      if (!bundle) {
        res.status(404).json({ error: `No tile ${req.params.tileId}` })
        return
      }
      const bounds = tileEnvelope(bundle)
      const [tileObservations, review, nearby] = await Promise.all([
        observationsForTile(bundle.tileId),
        db.collection(TILE_REVIEWS).doc(bundle.tileId).get(),
        // Decisions from other tiles that fall on this one (tiles overlap
        // at their edges, and an imagery re-run makes a new tile id): the
        // page draws them so a tree accepted once is not accepted twice.
        // Only the tile's latitude band is read -- a single-field range, so
        // no composite index -- and the rest is filtered here.
        db.collection(OBSERVATIONS).where('latitude', '>=', bounds.south).where('latitude', '<=', bounds.north).get(),
      ])
      // A prediction id is a cell of the chip, so another run's decision on
      // this tile can share one with this run's detection; only this run's
      // decisions attach to its detections, the rest draw as nearby ones.
      const runId = bundle.predictionLayer.runId
      const sameRun = (o: Record<string, unknown>) => o.tileId === bundle.tileId && o.runId === runId
      const nearbyObservations = [...nearby.docs.map(serializeObservation), ...tileObservations.filter((o) => !sameRun(o))]
        .filter((o, i, all) => all.findIndex((x) => x.id === o.id) === i)
        .filter((o) => o.city === bundle.city && !sameRun(o) && PUBLISHABLE.has(o.decision as Decision))
        .filter((o) => (o.longitude as number) >= bounds.west && (o.longitude as number) <= bounds.east)
        .map((o) => ({ ...o, ...lonLatToPixel(bundle, o.longitude as number, o.latitude as number) }))
      const reviewData = review.data()
      res.json({
        bundle: presentBundle(bundle),
        center: { latitude: (bounds.south + bounds.north) / 2, longitude: (bounds.west + bounds.east) / 2 },
        observations: tileObservations.filter(sameRun),
        nearbyObservations,
        review: { done: Boolean(reviewData?.done) && reviewData?.runId === runId, doneAt: toIso(reviewData?.doneAt), imageryVersion: reviewData?.imageryVersion ?? null, runId: reviewData?.runId ?? null },
      })
    } catch (error) {
      next(error)
    }
  })

  router.get('/tiles/:tileId/image', (req, res) => {
    const file = tiles.imagePath(req.params.tileId, runOf(req))
    if (!file) {
      res.status(404).json({ error: 'No image for that tile' })
      return
    }
    res.type('image/png').sendFile(file)
  })

  router.get('/tiles/:tileId/thumb', async (req, res, next) => {
    try {
      const data = await tiles.thumbnail(req.params.tileId, runOf(req))
      if (!data) {
        res.status(404).json({ error: 'No image for that tile' })
        return
      }
      res.type('image/jpeg').set('Cache-Control', 'no-cache').send(data)
    } catch (error) {
      next(error)
    }
  })

  router.put('/tiles/:tileId/observations/:predictionId', async (req, res, next) => {
    try {
      const bundle = tiles.get(req.params.tileId, runOf(req))
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
      const bundle = tiles.get(req.params.tileId, runOf(req))
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
      const bundle = tiles.get(req.params.tileId, runOf(req))
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
      const rows = snapshot.docs.map(serializeObservation).filter((o) => isPublishable(o))
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
      const bundle = tiles.get(req.params.tileId, runOf(req))
      if (!bundle) throw new Error(`No tile ${req.params.tileId}`)
      const pending = (await observationsForTile(bundle.tileId))
        .filter((o) => o.status === 'pending' && isPublishable(o))
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

  // -- Published satellite trees: corrected in place, never stacked -------

  router.get('/published/:treeId', async (req, res, next) => {
    try {
      const doc = await db.collection(PUBLISHED).doc(req.params.treeId).get()
      if (!doc.exists) {
        res.status(404).json({ error: `No published satellite tree ${req.params.treeId}` })
        return
      }
      const data = doc.data() ?? {}
      res.json({ ...exportRow(data), revision: data.revision ?? 0, withdrawnAt: isoOrNull(data.withdrawnAt), corrections: data.corrections ?? [] })
    } catch (error) {
      next(error)
    }
  })

  /** Apply a correction to one published tree inside a transaction. */
  async function correctTree(treeId: string, input: CorrectionInput, transaction: FirebaseFirestore.Transaction, now: string) {
    const ref = db.collection(PUBLISHED).doc(treeId)
    const snapshot = await transaction.get(ref)
    if (!snapshot.exists) throw new Error(`No published satellite tree ${treeId}`)
    const update = correctionUpdate(snapshot.data() ?? {}, input, actor, now)
    return { ref, update: { ...update, correctedAt: FieldValue.serverTimestamp(), correctedBy: actor } as Record<string, unknown> }
  }

  router.patch('/published/:treeId', async (req, res, next) => {
    try {
      const input = parseCorrection(req.body)
      const now = new Date().toISOString()
      const update = await db.runTransaction(async (transaction) => {
        const { ref, update } = await correctTree(req.params.treeId, input, transaction, now)
        transaction.update(ref, update as FirebaseFirestore.DocumentData)
        return update
      })
      const manifest = await rewritePublicExport()
      res.json({ ok: true, revision: update.revision, exported: manifest.count })
    } catch (error) {
      next(error)
    }
  })

  /** Take a tree out of the export (kept, marked, for the history and the freshness probe). */
  function withdraw(transaction: FirebaseFirestore.Transaction, treeId: string, data: Record<string, unknown>, reason: string) {
    if (data.withdrawnAt) throw new Error(`${treeId} is already withdrawn`)
    transaction.update(db.collection(PUBLISHED).doc(treeId), { withdrawnAt: FieldValue.serverTimestamp(), withdrawnBy: actor, withdrawReason: reason })
    if (data.observationId) transaction.update(db.collection(OBSERVATIONS).doc(String(data.observationId)), { status: 'withdrawn', updatedAt: FieldValue.serverTimestamp() })
  }

  router.post('/published/:treeId/withdraw', async (req, res, next) => {
    try {
      const reason = cleanString((req.body as { reason?: unknown })?.reason, MAX_NOTES, 'reason') ?? 'withdrawn by the reviewer'
      await db.runTransaction(async (transaction) => {
        const snapshot = await transaction.get(db.collection(PUBLISHED).doc(req.params.treeId))
        if (!snapshot.exists) throw new Error(`No published satellite tree ${req.params.treeId}`)
        withdraw(transaction, req.params.treeId, snapshot.data() ?? {}, reason)
      })
      const manifest = await rewritePublicExport()
      res.json({ ok: true, exported: manifest.count })
    } catch (error) {
      next(error)
    }
  })

  // Link one published satellite tree as a duplicate of another (an accept
  // from another run or tile that should have been "Same tree").  Private:
  // the export is not rewritten, and the row then waits in the stacked list
  // for Fold or Withdraw.
  router.post('/published/:treeId/link', async (req, res, next) => {
    try {
      const targetId = cleanString((req.body as { targetId?: unknown })?.targetId, 200, 'targetId') ?? ''
      await db.runTransaction(async (transaction) => {
        const ref = db.collection(PUBLISHED).doc(req.params.treeId)
        const [sourceSnap, targetSnap] = await Promise.all([
          transaction.get(ref),
          targetId ? transaction.get(db.collection(PUBLISHED).doc(targetId)) : Promise.resolve(null),
        ])
        const problem = linkProblem(sourceSnap.exists ? (sourceSnap.data() ?? {}) : null, targetSnap?.exists ? (targetSnap.data() ?? {}) : null, req.params.treeId, targetId)
        if (problem) throw Object.assign(new Error(problem), { status: 409 })
        transaction.update(ref, { duplicateOfTreeId: targetId, linkedAt: FieldValue.serverTimestamp(), linkedBy: actor })
      })
      res.json({ ok: true, treeId: req.params.treeId, duplicateOfTreeId: targetId })
    } catch (error) {
      next(error)
    }
  })

  // Fold a stacked row into the satellite tree it duplicates: the values a
  // person entered move onto the target, and the stacked row is withdrawn.
  router.post('/published/:treeId/fold', async (req, res, next) => {
    try {
      const now = new Date().toISOString()
      const result = await db.runTransaction(async (transaction) => {
        const stackedSnap = await transaction.get(db.collection(PUBLISHED).doc(req.params.treeId))
        if (!stackedSnap.exists) throw new Error(`No published satellite tree ${req.params.treeId}`)
        const stacked = stackedSnap.data() ?? {}
        const targetId = stacked.duplicateOfTreeId
        if (!isSatelliteTreeId(targetId)) throw new Error(`${req.params.treeId} does not duplicate a satellite tree`)
        const targetSnap = await transaction.get(db.collection(PUBLISHED).doc(String(targetId)))
        if (!targetSnap.exists) throw new Error(`Its target ${String(targetId)} is not a published satellite tree`)
        const changes = foldChanges(stacked, targetSnap.data() ?? {})
        // Every read before any write, as Firestore transactions require.
        const corrected = Object.keys(changes).length
          ? correctionUpdate(targetSnap.data() ?? {}, { changes, baseRevision: null }, actor, now)
          : null
        if (corrected) transaction.update(targetSnap.ref, { ...corrected, correctedAt: FieldValue.serverTimestamp(), correctedBy: actor })
        withdraw(transaction, req.params.treeId, stacked, `folded into ${String(targetId)}`)
        return { targetId: String(targetId), moved: Object.keys(changes) }
      })
      const manifest = await rewritePublicExport()
      res.json({ ok: true, ...result, exported: manifest.count })
    } catch (error) {
      next(error)
    }
  })

  // Rows already stacked on a satellite tree (published before that was refused).
  router.get('/stacked', async (_req, res, next) => {
    try {
      const snapshot = await db.collection(PUBLISHED).get()
      const byId = new Map(snapshot.docs.map((doc) => [doc.id, doc.data()]))
      const rows = snapshot.docs
        .map((doc) => doc.data())
        .filter((d) => !d.withdrawnAt && isSatelliteTreeId(d.duplicateOfTreeId))
        .map((d) => {
          const target = byId.get(String(d.duplicateOfTreeId))
          return {
            stacked: exportRow(d),
            target: target ? { ...exportRow(target), withdrawnAt: isoOrNull(target.withdrawnAt) } : null,
            wouldMove: target ? Object.keys(foldChanges(d, target)) : [],
          }
        })
      res.json(rows)
    } catch (error) {
      next(error)
    }
  })

  addFreezeRoutes(router, { db, cityCodes, rewritePublicExport })

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
