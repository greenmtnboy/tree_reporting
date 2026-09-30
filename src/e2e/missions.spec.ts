import { test, expect, type Page } from '@playwright/test'
import type { E2EFixtures } from '../src/lib/e2eFixtures'
import type { RankedTree } from '../src/lib/missions'

const boston: RankedTree[] = Array.from({ length: 6 }, (_, i) => ({
  treeId: `boston-${i}`, city: 'USBOS', species: 'Quercus rubra', treeForm: 'broadleaf',
  lat: 42.36, lng: -71.06, rarityTier: 'rare', trunkRank: i < 2 ? 1 : i,
  canopyRank: i === 0 ? 1 : null,
}))
const palms: RankedTree[] = Array.from({ length: 3 }, (_, i) => ({
  ...boston[i]!, treeId: `la-${i}`, city: 'USLAX', treeForm: 'palm',
  trunkRank: null, canopyRank: null,
}))

async function open(page: Page, seed: E2EFixtures, path = '/contributions?city=USBOS') {
  await page.addInitScript(seed => {
    window.__treeE2E = seed
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
  }, seed)
  await page.goto(`/#${path}`)
}
const seed: E2EFixtures = {
  user: { uid: 'mission-user', isAnonymous: false, providerIds: ['google.com'] },
  rankings: [...boston, ...palms], checkins: [], submissions: [],
}

for (const [label, viewport] of Object.entries({ desktop: { width: 1280, height: 800 }, mobile: { width: 390, height: 844 } })) {
  test.describe(`Missions — ${label}`, () => {
    test.use({ viewport })

    test('Boston champions, rarity progress, target links, and LA palms', async ({ page }) => {
      await open(page, seed)
      await expect(page.getByRole('heading', { name: 'Badges', exact: true })).toBeVisible()
      await expect(page.locator('[data-mission="trunk"]')).toContainText('widest recorded trunk')
      await expect(page.locator('[data-mission="rare"]')).toContainText('0 / 5')
      await expect(page.locator('[data-mission="trunk"] a')).toHaveAttribute('href', '#/?city=USBOS&tree=boston-0')
      await expect(page.locator('.badge-total-points')).toHaveText('0 points')
      await page.getByLabel('Mission city').selectOption('USLAX')
      await expect(page.locator('.mission').first()).toContainText('Palm Reader')
      await expect(page.locator('[data-mission="rare"]')).toHaveCount(0)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    })

    test('returning to desktop fetches new visits and awards points once per badge', async ({ page }) => {
      await open(page, seed)
      await expect(page.locator('[data-mission="rare"]')).toContainText('0 / 5')
      // A changed server response is represented by the fixture; no page reload.
      await page.evaluate(trees => {
        window.__treeE2E!.checkins = [...trees.slice(0, 5), trees[0]!].map(t => ({
          treeId: t.treeId, city: t.city, treeForm: t.treeForm,
          ranking: { rarityTier: t.rarityTier, trunkRank: t.trunkRank, canopyRank: t.canopyRank },
        }))
        window.dispatchEvent(new Event('focus'))
      }, boston)
      await expect(page.locator('[data-mission="rare"]')).toContainText('Badge earned')
      await expect(page.locator('[data-mission="rare"]')).toContainText('5 / 5')
      await expect(page.locator('.badge-total-points')).toHaveText('600 points')
      await page.getByRole('button', { name: 'Refresh contributions', exact: true }).click()
      await expect(page.locator('.badge-total-points')).toHaveText('600 points')
      await page.goto('/#/profile')
      await expect(page.locator('.total-points')).toHaveText('600 points')
    })

    test('old badges and points survive beyond the latest 50 visits', async ({ page }) => {
      await open(page, { ...seed, checkins: Array.from({ length: 251 }, (_, i) => ({
        treeId: `history-${i}`, city: 'USBOS', treeForm: i === 250 ? 'palm' : null,
      })) })
      await expect(page.locator('.badge.earned').filter({ hasText: 'Force of Nature' })).toBeVisible()
      await expect(page.locator('.badge.earned').filter({ hasText: 'Palm Reader' })).toBeVisible()
    })

    test('missing rankings can be retried without hiding existing badges', async ({ page }) => {
      await open(page, { ...seed, rankingsUnavailable: true })
      await expect(page.getByRole('status')).toContainText("City rankings aren't available yet")
      await expect(page.locator('.badges')).toBeVisible()
      await page.evaluate(() => { window.__treeE2E!.rankingsUnavailable = false })
      await page.getByRole('button', { name: 'Retry', exact: true }).click()
      await expect(page.locator('[data-mission="trunk"]')).toBeVisible()
    })
  })
}
