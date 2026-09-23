import express from 'express'
import { applicationDefault, getApps, initializeApp } from 'firebase-admin/app'
import { FieldValue, getFirestore, Timestamp } from 'firebase-admin/firestore'
import { getStorage } from 'firebase-admin/storage'
import path from 'node:path'
import sharp from 'sharp'

import { createSatelliteRouter, TileStore } from './satellite.ts'
import {
  assertCheckinPhotoPublishable,
  checkinPhotoListItem,
  checkinPhotoObjectPath,
  nextTreePhotos,
  treeDocKey,
} from './checkinPhotos.ts'
import {
  assertModificationPublishable,
  modificationExportRow,
  modificationListItem,
  modificationManifest,
  type PendingModification,
} from './modifications.ts'
import { assertCoordinatesAreInCity, CITY_CODES } from './submissionCity.ts'

const projectId = process.env.GOOGLE_CLOUD_PROJECT ?? 'sf-tree-reporting-prod'
const storageBucket = process.env.FIREBASE_STORAGE_BUCKET ?? 'sf-tree-reporting-submissions'
const publishedBucketName = process.env.PUBLISHED_BUCKET ?? 'sf-tree-reporting-published'
const port = Number(process.env.REVIEWER_PORT ?? 4174)
// Tile bundles for the satellite page, written by
// imagery_model/src/urban_tree_ml/tile_bundle_export.py.  The committed
// fixtures are two real tiles, one per wired city, so the page works on a
// fresh clone; point this at an export directory for a review session.
const tileDir = process.env.SATELLITE_TILE_DIR ?? path.join(import.meta.dirname, 'fixtures', 'tiles')
const firebaseApp = getApps()[0] ?? initializeApp({
  credential: applicationDefault(),
  projectId,
  storageBucket,
})
const db = getFirestore(firebaseApp)
const storage = getStorage(firebaseApp)
const bucket = storage.bucket(storageBucket)
const publishedBucket = storage.bucket(publishedBucketName)
const app = express()

const EXPORT_PATH = 'community/published_trees.ndjson'
const MANIFEST_PATH = 'community/manifest.json'
const MODIFICATIONS_EXPORT_PATH = 'community/tree_modifications.ndjson'
const MODIFICATIONS_MANIFEST_PATH = 'community/tree_modifications_manifest.json'
const PUBLISHED_PHOTO_MAX_DIM = 1600

app.use(express.json({ limit: '32kb' }))

// The satellite page: aerial tiles, model detections, inventory overlay,
// and a publish path of its own (see satellite.ts).
app.use('/api/satellite', createSatelliteRouter({
  db,
  publishedBucket,
  cityCodes: CITY_CODES,
  tiles: new TileStore(tileDir),
}))
app.get('/satellite', (_req, res) => {
  res.sendFile(path.join(import.meta.dirname, 'satellite_page.html'))
})

type PendingSubmission = {
  userId: string
  city: string
  photoPath: string
  additionalPhotoPaths?: string[]
  lat: number
  lng: number
  species?: string | null
  notes?: string | null
  submittedAt?: Timestamp
  status: 'pending' | 'published' | 'rejected'
}

function assertDecisionBody(body: unknown): { reason: string | null } {
  if (typeof body !== 'object' || body === null) return { reason: null }
  const value = (body as { reason?: unknown }).reason
  if (value == null || value === '') return { reason: null }
  if (typeof value !== 'string' || value.length > 500) throw new Error('Reason must be at most 500 characters')
  return { reason: value.trim() || null }
}

function localPhotoUrl(path: string): string {
  return `/api/photos?path=${encodeURIComponent(path)}`
}

function publishedPhotoUrl(objectPath: string): string {
  return `https://storage.googleapis.com/${publishedBucketName}/${objectPath}`
}

/**
 * Copy a submission photo into the public bucket, re-encoded.
 *
 * The web client already strips EXIF by re-encoding through a canvas, but the
 * storage rules only check `contentType`, so anything speaking the Storage API
 * can upload a JPEG with intact GPS tags. Publishing is the point where a photo
 * stops being private, so it is also where the guarantee has to hold: sharp
 * decodes to raw pixels and re-encodes, which carries no EXIF, IPTC, or XMP
 * across. `rotate()` first so the orientation tag is baked in before it is
 * dropped.
 */
async function publishPhoto(sourcePath: string, treeId: string, index: number): Promise<string> {
  return publishPhotoTo(sourcePath, `community/photos/${treeId}${index === 0 ? '' : `-${index}`}.jpg`)
}

async function publishPhotoTo(sourcePath: string, objectPath: string): Promise<string> {
  const [original] = await bucket.file(sourcePath).download()
  const cleaned = await sharp(original)
    .rotate()
    .resize({
      width: PUBLISHED_PHOTO_MAX_DIM,
      height: PUBLISHED_PHOTO_MAX_DIM,
      fit: 'inside',
      withoutEnlargement: true,
    })
    .jpeg({ quality: 85 })
    .toBuffer()

  await publishedBucket.file(objectPath).save(cleaned, {
    contentType: 'image/jpeg',
    metadata: { cacheControl: 'public, max-age=86400' },
  })
  return publishedPhotoUrl(objectPath)
}

/**
 * Rewrite the public approved-tree export the data pipeline reads.
 *
 * Firestore itself is not readable by the pipeline: `firestore.googleapis.com`
 * enforces IAM rather than security rules, so a public `allow read` rule still
 * returns 403 to an unauthenticated caller. Publishing a plain GCS object keeps
 * the pipeline credential-free and Firestore private.
 */
async function rewritePublicExport(): Promise<number> {
  const snapshot = await db.collection('publishedTrees').orderBy('publishedAt', 'asc').get()
  const lines: string[] = []
  let latestPublishedAt: string | null = null
  // Per city, so approving a tree in one city does not mark all fourteen city
  // Parquets stale and re-materialize every one of them.
  const latestPublishedAtByCity: Record<string, string> = {}
  for (const document of snapshot.docs) {
    const data = document.data()
    const publishedAt = data.publishedAt instanceof Timestamp ? data.publishedAt.toDate().toISOString() : null
    if (publishedAt) {
      latestPublishedAt = publishedAt
      const city = typeof data.city === 'string' ? data.city.toUpperCase() : null
      // Docs are ordered by publishedAt ascending, so the last write per city wins.
      if (city) latestPublishedAtByCity[city] = publishedAt
    }
    lines.push(JSON.stringify({
      treeId: data.treeId ?? document.id,
      city: data.city ?? null,
      species: data.species ?? null,
      latitude: data.latitude ?? null,
      longitude: data.longitude ?? null,
      photoUrl: data.photoUrl ?? null,
      additionalPhotoUrls: data.additionalPhotoUrls ?? [],
      publishedAt,
    }))
  }
  // No cache: the freshness probe must observe an approval on the next run.
  const writeOptions = { contentType: 'application/json', metadata: { cacheControl: 'no-cache' } }
  await publishedBucket.file(EXPORT_PATH).save(`${lines.join('\n')}\n`, writeOptions)
  await publishedBucket.file(MANIFEST_PATH).save(
    JSON.stringify({ count: lines.length, latestPublishedAt, latestPublishedAtByCity }),
    writeOptions,
  )
  return lines.length
}

/**
 * Rewrite the public export of approved tree modifications, in publish order.
 * Same reasoning as rewritePublicExport: the pipeline reads GCS, not Firestore.
 * The rows carry the change only: no user, notes, or photo.
 */
async function rewriteModificationExport(): Promise<number> {
  const snapshot = await db.collection('publishedTreeModifications').orderBy('publishedAt', 'asc').get()
  const rows = snapshot.docs.map((document) => {
    const data = document.data()
    const publishedAt = data.publishedAt instanceof Timestamp ? data.publishedAt.toDate().toISOString() : null
    return modificationExportRow(document.id, data, publishedAt)
  })
  const writeOptions = { contentType: 'application/json', metadata: { cacheControl: 'no-cache' } }
  const body = rows.map((row) => JSON.stringify(row)).join('\n')
  await publishedBucket.file(MODIFICATIONS_EXPORT_PATH).save(rows.length ? `${body}\n` : '', writeOptions)
  await publishedBucket.file(MODIFICATIONS_MANIFEST_PATH).save(
    JSON.stringify(modificationManifest(rows)),
    writeOptions,
  )
  return rows.length
}

app.get('/api/submissions', async (_req, res, next) => {
  try {
    const snapshot = await db.collection('submissions')
      .where('status', '==', 'pending')
      .orderBy('submittedAt', 'asc')
      .limit(100)
      .get()
    const rows = await Promise.all(snapshot.docs.map(async (document) => {
      const data = document.data() as PendingSubmission
      return {
        id: document.id,
        ...data,
        submittedAt: data.submittedAt?.toDate().toISOString() ?? null,
        photoUrl: localPhotoUrl(data.photoPath),
        additionalPhotoUrls: (data.additionalPhotoPaths ?? []).map(localPhotoUrl),
      }
    }))
    res.json(rows)
  } catch (error) {
    next(error)
  }
})

app.get('/api/photos', async (req, res, next) => {
  try {
    const path = typeof req.query.path === 'string' ? req.query.path : ''
    const privatePrefixes = ['submissions/', 'modifications/', 'checkins/']
    if (!privatePrefixes.some((prefix) => path.startsWith(prefix)) || path.includes('..')) {
      res.status(400).json({ error: 'Invalid submission photo path' })
      return
    }
    const file = bucket.file(path)
    const [metadata] = await file.getMetadata()
    res.type(metadata.contentType ?? 'image/jpeg')
    file.createReadStream().on('error', next).pipe(res)
  } catch (error) {
    next(error)
  }
})

app.post('/api/submissions/:id/approve', async (req, res, next) => {
  try {
    const submissionId = req.params.id
    const submissionRef = db.collection('submissions').doc(submissionId)
    const publishedRef = db.collection('publishedTrees').doc(`community-${submissionId}`)

    const pending = await submissionRef.get()
    if (!pending.exists) throw new Error('Submission not found')
    const submissionData = pending.data() as PendingSubmission
    if (submissionData.status !== 'pending') throw new Error(`Submission is already ${submissionData.status}`)
    if (!CITY_CODES.has(submissionData.city)) {
      throw new Error(`Submission city ${submissionData.city} is not a supported city code`)
    }
    assertCoordinatesAreInCity(submissionData.city, submissionData.lat, submissionData.lng)

    // Copy photos to the public bucket before the transaction records their
    // URLs. If the transaction then fails, the copies are simply unreferenced.
    const sourcePaths = [submissionData.photoPath, ...(submissionData.additionalPhotoPaths ?? [])]
    const publishedUrls = await Promise.all(
      sourcePaths.map((path, i) => publishPhoto(path, publishedRef.id, i)),
    )

    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(submissionRef)
      if (!snapshot.exists) throw new Error('Submission not found')
      const submission = snapshot.data() as PendingSubmission
      if (submission.status !== 'pending') throw new Error(`Submission is already ${submission.status}`)
      transaction.create(publishedRef, {
        treeId: publishedRef.id,
        submissionId,
        source: 'community',
        city: submission.city,
        latitude: submission.lat,
        longitude: submission.lng,
        species: submission.species ?? null,
        photoPath: submission.photoPath,
        additionalPhotoPaths: submission.additionalPhotoPaths ?? [],
        photoUrl: publishedUrls[0] ?? null,
        additionalPhotoUrls: publishedUrls.slice(1),
        submittedAt: submission.submittedAt ?? null,
        publishedAt: FieldValue.serverTimestamp(),
      })
      transaction.update(submissionRef, {
        status: 'published',
        reviewedAt: FieldValue.serverTimestamp(),
        rejectionReason: null,
        publishedTreeId: publishedRef.id,
      })
    })

    const published = await rewritePublicExport()
    res.json({ ok: true, publishedTreeId: publishedRef.id, published })
  } catch (error) {
    next(error)
  }
})

app.post('/api/submissions/:id/reject', async (req, res, next) => {
  try {
    const { reason } = assertDecisionBody(req.body)
    const submissionRef = db.collection('submissions').doc(req.params.id)
    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(submissionRef)
      if (!snapshot.exists) throw new Error('Submission not found')
      const submission = snapshot.data() as PendingSubmission
      if (submission.status !== 'pending') throw new Error(`Submission is already ${submission.status}`)
      transaction.update(submissionRef, {
        status: 'rejected',
        reviewedAt: FieldValue.serverTimestamp(),
        rejectionReason: reason,
      })
    })
    res.json({ ok: true })
  } catch (error) {
    next(error)
  }
})

app.get('/api/modifications', async (_req, res, next) => {
  try {
    const snapshot = await db.collection('treeModifications')
      .where('status', '==', 'pending')
      .orderBy('submittedAt', 'asc')
      .limit(100)
      .get()
    res.json(snapshot.docs.map((document) => modificationListItem(document.id, document.data(), localPhotoUrl)))
  } catch (error) {
    next(error)
  }
})

app.post('/api/modifications/:id/approve', async (req, res, next) => {
  try {
    const modificationId = req.params.id
    const modificationRef = db.collection('treeModifications').doc(modificationId)
    const publishedRef = db.collection('publishedTreeModifications').doc(modificationId)
    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(modificationRef)
      if (!snapshot.exists) throw new Error('Modification not found')
      const modification = snapshot.data() as PendingModification & { submittedAt?: Timestamp }
      assertModificationPublishable(modification)
      const isUpdate = modification.kind === 'update'
      transaction.create(publishedRef, {
        modificationId,
        treeId: modification.treeId,
        city: modification.city,
        kind: modification.kind,
        missingReason: isUpdate ? null : (modification.missingReason ?? null),
        latitude: isUpdate ? (modification.proposedLat ?? null) : null,
        longitude: isUpdate ? (modification.proposedLng ?? null) : null,
        species: isUpdate ? (modification.proposedSpecies?.trim() || null) : null,
        dbhInches: isUpdate ? (modification.proposedDbhInches ?? null) : null,
        submittedAt: modification.submittedAt ?? null,
        publishedAt: FieldValue.serverTimestamp(),
      })
      transaction.update(modificationRef, {
        status: 'published',
        reviewedAt: FieldValue.serverTimestamp(),
        rejectionReason: null,
      })
    })
    const published = await rewriteModificationExport()
    res.json({ ok: true, published })
  } catch (error) {
    next(error)
  }
})

app.post('/api/modifications/:id/reject', async (req, res, next) => {
  try {
    const { reason } = assertDecisionBody(req.body)
    const modificationRef = db.collection('treeModifications').doc(req.params.id)
    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(modificationRef)
      if (!snapshot.exists) throw new Error('Modification not found')
      const modification = snapshot.data() as PendingModification
      if (modification.status !== 'pending') throw new Error(`Modification is already ${modification.status}`)
      transaction.update(modificationRef, {
        status: 'rejected',
        reviewedAt: FieldValue.serverTimestamp(),
        rejectionReason: reason,
      })
    })
    res.json({ ok: true })
  } catch (error) {
    next(error)
  }
})

app.post('/api/modifications/republish', async (_req, res, next) => {
  try {
    res.json({ ok: true, published: await rewriteModificationExport() })
  } catch (error) {
    next(error)
  }
})

type PendingCheckinPhoto = {
  userId: string
  treeId: string
  city?: string
  photoPath?: string | null
  photoReview?: 'pending' | 'published' | 'rejected'
  species?: string | null
  lat?: number
  lng?: number
  treeLat?: number
  treeLng?: number
  distanceMeters?: number | null
  at?: Timestamp
}

app.get('/api/checkin-photos', async (_req, res, next) => {
  try {
    const snapshot = await db.collection('checkins')
      .where('photoReview', '==', 'pending')
      .orderBy('at', 'asc')
      .limit(100)
      .get()
    res.json(snapshot.docs.map((document) => checkinPhotoListItem(document.id, document.data(), localPhotoUrl)))
  } catch (error) {
    next(error)
  }
})

app.post('/api/checkin-photos/:id/approve', async (req, res, next) => {
  try {
    const checkinRef = db.collection('checkins').doc(req.params.id)
    const pending = await checkinRef.get()
    const checkin = assertCheckinPhotoPublishable(pending.data() as PendingCheckinPhoto | undefined)

    // Re-encode into the public bucket before the transaction records the URL;
    // if the transaction then fails the copy is simply unreferenced.
    const photoUrl = await publishPhotoTo(checkin.photoPath, checkinPhotoObjectPath(checkin.treeId, checkinRef.id))
    const treePhotosRef = db.collection('treePhotos').doc(treeDocKey(checkin.treeId))

    await db.runTransaction(async (transaction) => {
      const [snapshot, treePhotos] = await Promise.all([transaction.get(checkinRef), transaction.get(treePhotosRef)])
      const current = snapshot.data() as PendingCheckinPhoto | undefined
      if (current?.photoReview !== 'pending') throw new Error(`Photo is already ${current?.photoReview ?? 'gone'}`)
      transaction.set(treePhotosRef, {
        treeId: checkin.treeId,
        ...nextTreePhotos(treePhotos.data(), photoUrl),
        latestPublishedAt: FieldValue.serverTimestamp(),
      })
      transaction.update(checkinRef, {
        photoReview: 'published',
        publishedPhotoUrl: photoUrl,
        photoReviewedAt: FieldValue.serverTimestamp(),
      })
    })
    res.json({ ok: true, photoUrl })
  } catch (error) {
    next(error)
  }
})

app.post('/api/checkin-photos/:id/reject', async (req, res, next) => {
  try {
    const { reason } = assertDecisionBody(req.body)
    const checkinRef = db.collection('checkins').doc(req.params.id)
    await db.runTransaction(async (transaction) => {
      const snapshot = await transaction.get(checkinRef)
      if (!snapshot.exists) throw new Error('Check-in not found')
      const checkin = snapshot.data() as PendingCheckinPhoto
      if (checkin.photoReview !== 'pending') throw new Error(`Photo is already ${checkin.photoReview ?? 'not offered'}`)
      transaction.update(checkinRef, {
        photoReview: 'rejected',
        photoRejectionReason: reason,
        photoReviewedAt: FieldValue.serverTimestamp(),
      })
    })
    res.json({ ok: true })
  } catch (error) {
    next(error)
  }
})

app.get('/photos', (_req, res) => {
  res.sendFile(path.join(import.meta.dirname, 'checkin_photos_page.html'))
})

app.get('/modifications', (_req, res) => {
  res.sendFile(path.join(import.meta.dirname, 'modifications_page.html'))
})

// Rebuild the export from Firestore without approving anything — for the first
// run, or to recover if an approval committed but the export write failed.
app.post('/api/republish', async (_req, res, next) => {
  try {
    res.json({ ok: true, published: await rewritePublicExport() })
  } catch (error) {
    next(error)
  }
})

app.get('/', (_req, res) => {
  res.type('html').send(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Tree submission review</title>
<style>
body{margin:0;background:#0b0f0d;color:#d7eadb;font:15px system-ui}main{max-width:1100px;margin:auto;padding:28px}
h1{font-size:24px}.queue{display:grid;gap:18px}.card{display:grid;grid-template-columns:minmax(260px,420px) 1fr;gap:20px;padding:18px;border:1px solid #35513d;background:#111914}
img{width:100%;max-height:420px;object-fit:contain;background:#050806}.thumbs{display:flex;gap:8px;margin-top:8px}.thumbs img{width:80px;height:80px}
dl{display:grid;grid-template-columns:110px 1fr;gap:8px;margin:0}dt{color:#8cab94}dd{margin:0;overflow-wrap:anywhere}
.actions{display:flex;gap:10px;margin-top:18px}button{border:1px solid #6da87b;background:#1d3624;color:#e7f5ea;padding:9px 14px;cursor:pointer}
button.reject{border-color:#a86d6d;background:#361d1d}.empty{color:#8cab94}@media(max-width:720px){.card{grid-template-columns:1fr}}
nav{margin-bottom:14px}nav a{color:#8cab94;margin-right:14px}
</style></head><body><main><nav><a href="/">Photo submissions</a><a href="/modifications">Tree reports</a><a href="/photos">Tree photos</a><a href="/satellite">Satellite tiles</a><a href="/satellite#queue">Satellite publish queue</a></nav><h1>Pending tree submissions</h1><p id="status">Loading…</p><section id="queue" class="queue"></section></main>
<script>
const queue=document.querySelector('#queue'),status=document.querySelector('#status');
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){status.textContent='Loading…';const r=await fetch('/api/submissions');if(!r.ok)throw new Error(await r.text());const rows=await r.json();
status.textContent=rows.length?rows.length+' awaiting review':'Queue is empty';
queue.innerHTML=rows.map(s=>\`<article class="card" data-id="\${s.id}"><div><img src="\${esc(s.photoUrl)}" alt="Submitted tree"><div class="thumbs">\${s.additionalPhotoUrls.map(u=>\`<img src="\${esc(u)}" alt="Additional view">\`).join('')}</div></div><div><dl><dt>City</dt><dd>\${esc(s.city)}</dd><dt>Location</dt><dd><a href="https://www.openstreetmap.org/?mlat=\${s.lat}&mlon=\${s.lng}#map=20/\${s.lat}/\${s.lng}" target="_blank">\${s.lat}, \${s.lng}</a></dd><dt>Species</dt><dd>\${esc(s.species)}</dd><dt>Notes</dt><dd>\${esc(s.notes)}</dd><dt>Submitted</dt><dd>\${esc(s.submittedAt)}</dd><dt>User</dt><dd>\${esc(s.userId)}</dd></dl><div class="actions"><button data-action="approve">Approve</button><button class="reject" data-action="reject">Reject</button></div></div></article>\`).join('')}
async function decide(id,action){let body={};if(action==='reject'){const reason=prompt('Optional rejection reason')??null;if(reason===null)return;body={reason}}const r=await fetch('/api/submissions/'+id+'/'+action,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error(await r.text());await load()}
queue.addEventListener('click',e=>{const b=e.target.closest('button[data-action]');if(!b)return;const card=b.closest('[data-id]');b.disabled=true;decide(card.dataset.id,b.dataset.action).catch(err=>{alert(err.message);b.disabled=false})});
load().catch(err=>status.textContent='Could not load queue: '+err.message);
</script></body></html>`)
})

app.use((error: Error & { status?: number }, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error(error)
  res.status(error.status ?? 400).json({ error: error.message })
})

app.listen(port, '127.0.0.1', () => {
  console.log(`Tree reviewer listening at http://127.0.0.1:${port}`)
  console.log(`Satellite tiles from ${tileDir} at http://127.0.0.1:${port}/satellite`)
  console.log(`Using Firebase project ${projectId}`)
})
