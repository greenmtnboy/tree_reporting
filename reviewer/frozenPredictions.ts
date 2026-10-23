/**
 * Freezing confirmed bulk predictions.
 *
 * A bulk prediction (`pred-…`, the PREDICTED_{code} partition) belongs to one
 * model run: the next run replaces the city's staged file whole, and every id
 * with it.  A person who checks in on a predicted tree, or reports a change to
 * one, has confirmed that the tree is there, and that must outlive the run.
 * So the sweep here copies each confirmed prediction into `satelliteTrees`,
 * where the public satellite export publishes it as a SATELLITE_{code} row
 * that no later run touches and that absorbs whatever the next run predicts at
 * the same spot (tree_dedup.preql's merge cell, as for any satellite tree).
 *
 * The frozen tree takes its own id, `sat-` + sha1 of the prediction's: tree_id
 * keys the merge, and the prediction keeps its row until the next upload.  Its
 * check-in counter and published photos move to the new id with it, once,
 * inside the transaction that creates the doc.  A prediction a satellite tree
 * already covers is recorded as a withdrawn doc pointing at that tree, so the
 * sweep stays idempotent and the counter moves there instead.  A doc the
 * reviewer withdrew is never frozen again.
 *
 * Machines never freeze a tree: an automated review is regenerable and belongs
 * in the staged parquet, not here.
 */

import express from 'express'
import { createHash } from 'node:crypto'
import { FieldValue, type Firestore } from 'firebase-admin/firestore'
import { MAX_TREE_PHOTOS, treeDocKey } from './checkinPhotos.ts'
import { DUPLICATE_RADIUS_M, haversineM, PUBLISHED, type ExportManifest } from './satellite.ts'

export const PREDICTED_ID_PREFIX = 'pred-'
export const FROZEN_ORIGIN = 'bulk-confirmed'

export type ConfirmationKind = 'checkin' | 'report'

/** One person's confirmation of a predicted tree, as the app stored it. */
export interface Confirmation {
  predictedTreeId: string
  kind: ConfirmationKind
  id: string
  city: string
  latitude: number
  longitude: number
  species: string | null
  at: string | null
}

export function isPredictedTreeId(treeId: unknown): treeId is string {
  return typeof treeId === 'string' && treeId.startsWith(PREDICTED_ID_PREFIX)
}

/** The frozen tree's id: stable per prediction, so a second sweep finds it. */
export function frozenTreeId(predictedTreeId: string): string {
  return `sat-${createHash('sha1').update(`frozen|${predictedTreeId}`).digest('hex').slice(0, 20)}`
}

function isoOf(value: unknown): string | null {
  if (value && typeof value === 'object' && 'toDate' in value && typeof (value as { toDate: unknown }).toDate === 'function') {
    return (value as { toDate(): Date }).toDate().toISOString()
  }
  return typeof value === 'string' ? value : null
}

function cleanSpecies(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

/** A check-in on a predicted tree (`checkins/{id}`), or null when it is not one. */
export function confirmationFromCheckin(id: string, data: Record<string, unknown>): Confirmation | null {
  if (!isPredictedTreeId(data.treeId)) return null
  const latitude = data.treeLat, longitude = data.treeLng
  if (typeof latitude !== 'number' || typeof longitude !== 'number' || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
  return {
    predictedTreeId: data.treeId, kind: 'checkin', id,
    city: String(data.city ?? '').toUpperCase(), latitude, longitude,
    species: cleanSpecies(data.species), at: isoOf(data.at),
  }
}

/**
 * A tree report on a predicted tree (`treeModifications/{id}`).  An update
 * report says the tree stands there; a "missing" report says the opposite and
 * a rejected one says nothing, so neither confirms.
 */
export function confirmationFromReport(id: string, data: Record<string, unknown>): Confirmation | null {
  if (!isPredictedTreeId(data.treeId) || data.kind !== 'update' || data.status === 'rejected') return null
  const latitude = data.treeLat, longitude = data.treeLng
  if (typeof latitude !== 'number' || typeof longitude !== 'number' || !Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
  return {
    predictedTreeId: data.treeId, kind: 'report', id,
    city: String(data.city ?? '').toUpperCase(), latitude, longitude,
    species: cleanSpecies(data.currentSpecies), at: isoOf(data.submittedAt),
  }
}

/** The earliest confirmation of each predicted tree: the one that froze it. */
export function firstConfirmations(confirmations: Confirmation[]): Confirmation[] {
  const first = new Map<string, Confirmation>()
  for (const c of confirmations) {
    const current = first.get(c.predictedTreeId)
    if (!current || (c.at ?? '￿') < (current.at ?? '￿')) first.set(c.predictedTreeId, c)
  }
  return [...first.values()].sort((a, b) => a.predictedTreeId.localeCompare(b.predictedTreeId))
}

/** A live published satellite tree within DUPLICATE_RADIUS_M of the prediction, if any. */
export function coveringTree(
  confirmation: Confirmation,
  published: { id: string; data: Record<string, unknown> }[],
): string | null {
  let best: { id: string; distance: number } | null = null
  for (const { id, data } of published) {
    if (data.withdrawnAt || String(data.city ?? '').toUpperCase() !== confirmation.city) continue
    if (typeof data.latitude !== 'number' || typeof data.longitude !== 'number') continue
    const distance = haversineM(confirmation.longitude, confirmation.latitude, data.longitude, data.latitude)
    if (distance <= DUPLICATE_RADIUS_M && (!best || distance < best.distance)) best = { id, distance }
  }
  return best?.id ?? null
}

/**
 * The `satelliteTrees` record of a frozen prediction.  The position and name
 * are the ones the person saw on the card; nothing here is a measurement, and
 * the run's provenance is not kept (the id names the prediction).
 */
export function frozenTreeFrom(confirmation: Confirmation): Record<string, unknown> {
  return {
    treeId: frozenTreeId(confirmation.predictedTreeId),
    observationId: null,
    city: confirmation.city,
    latitude: confirmation.latitude,
    longitude: confirmation.longitude,
    positionRole: 'crown_center',
    species: confirmation.species,
    speciesSource: 'model',
    predictedDbhInches: null,
    measuredDbhInches: null,
    predictedCrownWidthM: null,
    crownWidthMethod: null,
    humanCrownWidthM: null,
    duplicateOfTreeId: null,
    tileId: null,
    imageryVersion: null,
    runId: null,
    predictionId: null,
    acquisitionDate: null,
    origin: FROZEN_ORIGIN,
    predictedTreeId: confirmation.predictedTreeId,
    confirmedBy: confirmation.kind,
    confirmationId: confirmation.id,
    confirmedAt: confirmation.at,
  }
}

/** The target's photo list with the prediction's photos added, newest first, capped. */
export function mergedTreePhotos(target: Record<string, unknown> | undefined, source: Record<string, unknown> | undefined): string[] {
  const urls = (value: unknown) => (Array.isArray(value) ? value.filter((u): u is string => typeof u === 'string') : [])
  return [...new Set([...urls(target?.photoUrls), ...urls(source?.photoUrls)])].slice(0, MAX_TREE_PHOTOS)
}

export interface FreezePlan {
  freeze: Confirmation[]
  covered: { confirmation: Confirmation; treeId: string }[]
  alreadyFrozen: number
  skipped: { predictedTreeId: string; reason: string }[]
}

/** Sort the confirmations into what a sweep would do, given what is already published. */
export function planFreeze(
  confirmations: Confirmation[],
  published: { id: string; data: Record<string, unknown> }[],
  cityCodes: Set<string>,
): FreezePlan {
  const ids = new Set(published.map((p) => p.id))
  const plan: FreezePlan = { freeze: [], covered: [], alreadyFrozen: 0, skipped: [] }
  for (const confirmation of firstConfirmations(confirmations)) {
    if (ids.has(frozenTreeId(confirmation.predictedTreeId))) {
      plan.alreadyFrozen += 1
    } else if (!cityCodes.has(confirmation.city)) {
      plan.skipped.push({ predictedTreeId: confirmation.predictedTreeId, reason: `${confirmation.city || 'no city'} is not a supported city code` })
    } else {
      const treeId = coveringTree(confirmation, published)
      if (treeId) plan.covered.push({ confirmation, treeId })
      else plan.freeze.push(confirmation)
    }
  }
  return plan
}

export interface FreezeDeps {
  db: Firestore
  cityCodes: Set<string>
  rewritePublicExport(): Promise<ExportManifest>
}

// Range on the id prefix: one single-field index, no composite one.
const PREDICTED_RANGE = [PREDICTED_ID_PREFIX, 'pred.'] as const

async function loadConfirmations(db: Firestore): Promise<Confirmation[]> {
  const [checkins, reports] = await Promise.all(['checkins', 'treeModifications'].map((name) =>
    db.collection(name).where('treeId', '>=', PREDICTED_RANGE[0]).where('treeId', '<', PREDICTED_RANGE[1]).get()))
  return [
    ...checkins.docs.map((d) => confirmationFromCheckin(d.id, d.data())),
    ...reports.docs.map((d) => confirmationFromReport(d.id, d.data())),
  ].filter((c): c is Confirmation => c !== null)
}

async function currentPlan(deps: FreezeDeps): Promise<FreezePlan> {
  const [confirmations, published] = await Promise.all([
    loadConfirmations(deps.db),
    deps.db.collection(PUBLISHED).get().then((s) => s.docs.map((d) => ({ id: d.id, data: d.data() }))),
  ])
  return planFreeze(confirmations, published, deps.cityCodes)
}

/** Create one frozen (or covered) record and move the prediction's counter and photos to `targetId`. */
async function freezeOne(db: Firestore, confirmation: Confirmation, record: Record<string, unknown>, targetId: string): Promise<boolean> {
  const frozenRef = db.collection(PUBLISHED).doc(frozenTreeId(confirmation.predictedTreeId))
  const statsFrom = db.collection('treeCheckinStats').doc(treeDocKey(confirmation.predictedTreeId))
  const statsTo = db.collection('treeCheckinStats').doc(treeDocKey(targetId))
  const photosFrom = db.collection('treePhotos').doc(treeDocKey(confirmation.predictedTreeId))
  const photosTo = db.collection('treePhotos').doc(treeDocKey(targetId))
  return db.runTransaction(async (transaction) => {
    const [frozen, statsSource, photosSource, photosTarget] = await Promise.all(
      [frozenRef, statsFrom, photosFrom, photosTo].map((ref) => transaction.get(ref)))
    if (frozen.exists) return false
    transaction.create(frozenRef, { ...record, publishedAt: FieldValue.serverTimestamp() })
    const count = Number(statsSource.data()?.count ?? 0)
    if (count > 0) {
      transaction.set(statsTo, { treeId: targetId, count: FieldValue.increment(count), lastCheckinId: statsSource.data()?.lastCheckinId ?? null }, { merge: true })
    }
    if (photosSource.exists) {
      const photoUrls = mergedTreePhotos(photosTarget.data(), photosSource.data())
      transaction.set(photosTo, { treeId: targetId, photoUrls, count: photoUrls.length, latestPublishedAt: FieldValue.serverTimestamp() })
    }
    return true
  })
}

function planSummary(plan: FreezePlan) {
  return {
    toFreeze: plan.freeze.map((c) => ({ predictedTreeId: c.predictedTreeId, treeId: frozenTreeId(c.predictedTreeId), city: c.city, confirmedBy: c.kind })),
    covered: plan.covered.map(({ confirmation, treeId }) => ({ predictedTreeId: confirmation.predictedTreeId, treeId })),
    alreadyFrozen: plan.alreadyFrozen,
    skipped: plan.skipped,
  }
}

/** `GET` shows what a sweep would do; `POST` does it and rewrites the export. */
export function addFreezeRoutes(router: express.Router, deps: FreezeDeps): void {
  router.get('/freeze-confirmed', async (_req, res, next) => {
    try {
      const plan = await currentPlan(deps)
      res.json({ pending: plan.freeze.length + plan.covered.length, ...planSummary(plan) })
    } catch (error) {
      next(error)
    }
  })

  router.post('/freeze-confirmed', async (_req, res, next) => {
    try {
      const plan = await currentPlan(deps)
      let frozen = 0, covered = 0
      for (const confirmation of plan.freeze) {
        const record = frozenTreeFrom(confirmation)
        if (await freezeOne(deps.db, confirmation, record, String(record.treeId))) frozen += 1
      }
      for (const { confirmation, treeId } of plan.covered) {
        // Recorded withdrawn, so the export skips it and the next sweep does not move the counter again.
        const record = { ...frozenTreeFrom(confirmation), duplicateOfTreeId: treeId, withdrawnAt: FieldValue.serverTimestamp(), withdrawReason: `already published as ${treeId}` }
        if (await freezeOne(deps.db, confirmation, record, treeId)) covered += 1
      }
      const manifest = frozen + covered ? await deps.rewritePublicExport() : null
      res.json({ ok: true, ...planSummary(plan), frozenNow: frozen, coveredNow: covered, exported: manifest?.count ?? null })
    } catch (error) {
      next(error)
    }
  })
}

/** The tree a frozen record stands for: itself, or the satellite tree that already covered it. */
export function frozenTarget(record: Record<string, unknown>): string {
  return typeof record.duplicateOfTreeId === 'string' && record.duplicateOfTreeId ? record.duplicateOfTreeId : String(record.treeId)
}

/** Where a photo or counter for `treeId` belongs now: a frozen prediction's tree, else the id itself. */
export async function currentTreeId(db: Firestore, treeId: string): Promise<string> {
  if (!isPredictedTreeId(treeId)) return treeId
  const frozen = await db.collection(PUBLISHED).doc(frozenTreeId(treeId)).get()
  return frozen.exists ? frozenTarget(frozen.data() ?? {}) : treeId
}
