import { test, expect } from '@playwright/test'
import type { E2EFixtures } from '../src/lib/e2eFixtures'

/**
 * The contributions page's two review-queue surfaces: where each check-in
 * photo offered for its tree has got to, and the user's tree reports. Seeded
 * through the fixture seam like achievements.spec.ts; see src/lib/e2eFixtures.ts.
 */

const REPORTER: E2EFixtures = {
  user: {
    uid: 'e2e-report-user',
    displayName: 'Report Tester',
    email: 'report@example.com',
    isAnonymous: false,
    providerIds: ['google.com'],
  },
  submissions: [],
  checkins: [
    { city: 'USSFO', treeId: 'sf-1', at: '2026-03-05T12:00:00', hasPhoto: true, photoReview: 'pending' },
    { city: 'USSFO', treeId: 'sf-2', at: '2026-03-04T12:00:00', hasPhoto: true, photoReview: 'published' },
    { city: 'USSFO', treeId: 'sf-3', at: '2026-03-03T12:00:00', hasPhoto: true, photoReview: 'rejected' },
    { city: 'USSFO', treeId: 'sf-4', at: '2026-03-02T12:00:00', hasPhoto: true },
  ],
  modifications: [
    { kind: 'missing', treeId: 'sf-10', city: 'USSFO', submittedAt: '2026-03-06T09:00:00', status: 'pending' },
    {
      kind: 'update',
      treeId: 'sf-11',
      city: 'USSFO',
      submittedAt: '2026-03-05T09:00:00',
      status: 'published',
      proposedSpecies: 'Acer rubrum',
    },
  ],
}

async function openContributions(page: import('@playwright/test').Page, fixtures: E2EFixtures) {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.addInitScript((seed) => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
    window.__treeE2E = seed as never
  }, fixtures)
  await page.goto('/#/contributions')
}

test('check-in photos show where their review has got to', async ({ page }) => {
  await openContributions(page, REPORTER)
  await expect(page.getByText('Tree photo awaiting review')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Tree photo is on the map')).toBeVisible()
  await expect(page.getByText('Tree photo not published')).toBeVisible()
  // A photo kept private was never offered, so it has no review line.
  await expect(page.locator('.checkin-distance', { hasText: 'Tree photo' })).toHaveCount(3)
})

test('tree reports list each report with its status', async ({ page }) => {
  await openContributions(page, REPORTER)
  const list = page.getByTestId('modification-list')
  await expect(list).toBeVisible({ timeout: 20_000 })
  const items = list.locator('li')
  await expect(items).toHaveCount(2)
  await expect(items.nth(0)).toContainText('Reported missing')
  await expect(items.nth(0)).toContainText('pending')
  await expect(items.nth(0)).toContainText('sf-10')
  await expect(items.nth(1)).toContainText('species → Acer rubrum')
  await expect(items.nth(1)).toContainText('published')
})

test('with no reports, the page says how to make one', async ({ page }) => {
  await openContributions(page, { ...REPORTER, modifications: [] })
  await expect(page.getByText('No reports yet.')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText('Missing or mapped wrong? Report it')).toBeVisible()
})
