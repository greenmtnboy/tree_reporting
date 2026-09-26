import { readFileSync } from 'node:fs'
import path from 'node:path'
import { after, before, describe, test } from 'node:test'

import {
  assertFails,
  assertSucceeds,
  initializeTestEnvironment,
  type RulesTestEnvironment,
} from '@firebase/rules-unit-testing'
import { ref, uploadBytes, type FirebaseStorage } from 'firebase/storage'

const RULES = readFileSync(path.join(import.meta.dirname, '..', 'storage.rules'), 'utf8')

let env: RulesTestEnvironment

before(async () => {
  env = await initializeTestEnvironment({ projectId: 'demo-tree-rules', storage: { rules: RULES } })
})
after(async () => {
  await env?.cleanup()
})

const as = (uid: string) => env.authenticatedContext(uid).storage() as unknown as FirebaseStorage
const JPEG = new Uint8Array([0xff, 0xd8, 0xff, 0xe0])

describe('photo uploads', () => {
  for (const folder of ['submissions', 'checkins', 'modifications']) {
    test(`${folder}: an image in your own folder uploads`, async () => {
      await assertSucceeds(uploadBytes(ref(as('alice'), `${folder}/alice/p.jpeg`), JPEG, { contentType: 'image/jpeg' }))
    })

    test(`${folder}: someone else's folder, or a non-image, is refused`, async () => {
      await assertFails(uploadBytes(ref(as('alice'), `${folder}/bob/p.jpeg`), JPEG, { contentType: 'image/jpeg' }))
      await assertFails(uploadBytes(ref(as('alice'), `${folder}/alice/p.html`), JPEG, { contentType: 'text/html' }))
    })
  }
})
