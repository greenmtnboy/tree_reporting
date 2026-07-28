import express from 'express'
import { applicationDefault, getApps, initializeApp } from 'firebase-admin/app'
import { FieldValue, getFirestore, Timestamp } from 'firebase-admin/firestore'
import { getStorage } from 'firebase-admin/storage'

const projectId = process.env.GOOGLE_CLOUD_PROJECT ?? 'sf-tree-reporting-prod'
const storageBucket = process.env.FIREBASE_STORAGE_BUCKET ?? 'sf-tree-reporting-submissions'
const port = Number(process.env.REVIEWER_PORT ?? 4174)
const firebaseApp = getApps()[0] ?? initializeApp({
  credential: applicationDefault(),
  projectId,
  storageBucket,
})
const db = getFirestore(firebaseApp)
const bucket = getStorage(firebaseApp).bucket(storageBucket)
const app = express()

app.use(express.json({ limit: '32kb' }))

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
    if (!path.startsWith('submissions/') || path.includes('..')) {
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
    res.json({ ok: true, publishedTreeId: publishedRef.id })
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
</style></head><body><main><h1>Pending tree submissions</h1><p id="status">Loading…</p><section id="queue" class="queue"></section></main>
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

app.use((error: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  console.error(error)
  res.status(400).json({ error: error.message })
})

app.listen(port, '127.0.0.1', () => {
  console.log(`Tree reviewer listening at http://127.0.0.1:${port}`)
  console.log(`Using Firebase project ${projectId}`)
})
