import { test, expect, type Page } from '@playwright/test'
import { ACHIEVEMENTS } from '../src/lib/achievements'
import type { E2EFixtures } from '../src/lib/e2eFixtures'

/**
 * Achievements at both viewports.
 *
 * Badges are derived from a signed-in user's Firestore contributions, so the
 * suite seeds the session and the contribution lists through the fixture seam
 * in `src/lib/e2eFixtures.ts` — see the comment there. That seam only exists in
 * an e2e build (`pnpm build:e2e`), which Playwright's webServer runs for you;
 * `assertE2EBuild` below turns a stale preview server into a clear failure
 * instead of a mystifying "sign in to see your contributions".
 *
 * Timestamps are written without a zone suffix on purpose: they are parsed as
 * browser-local time, which keeps the time-of-day badges (Dawn Chorus, Night
 * Owl) and the distinct-day count deterministic wherever the tests run.
 */

const DESKTOP = { width: 1280, height: 800 }
const MOBILE = { width: 390, height: 844 }

const CONTRIBUTOR: E2EFixtures = {
  user: {
    uid: 'e2e-badge-user',
    displayName: 'Badge Tester',
    email: 'badge@example.com',
    isAnonymous: false,
    providerIds: ['google.com'],
  },
  submissions: [
    { city: 'USSFO', species: 'Platanus x hispanica', submittedAt: '2026-03-02T10:15:00', photoCount: 3 },
    { city: 'USSFO', species: 'Ginkgo biloba', submittedAt: '2026-03-04T11:00:00' },
  ],
  checkins: [
    {
      city: 'USSFO',
      at: '2026-03-05T12:00:00',
      hasPhoto: true,
      species: 'Washingtonia robusta',
      treeForm: 'palm',
      dbhInches: 12,
      plantYear: 2005,
      speciesCityCount: 4200,
    },
    {
      city: 'USNYC',
      at: '2026-03-06T13:30:00',
      species: 'Sequoia sempervirens',
      treeForm: 'conifer',
      dbhInches: 62,
      plantYear: 1898,
      speciesCityCount: 1,
    },
    {
      city: 'USNYC',
      at: '2026-03-07T14:00:00',
      species: 'Quercus rubra',
      treeForm: 'broadleaf',
      dbhInches: 20,
      plantYear: 1990,
      speciesCityCount: 900,
    },
  ],
}

/** What CONTRIBUTOR earns, spelled out rather than recomputed from the lib. */
const EARNED_TITLES = [
  'First Roots', // 2 submissions
  'Say Hello', // 3 check-ins
  'Portrait Mode', // one check-in has a photo
  'Nice to Meet You', // every contribution names a species
  'Full Coverage', // one submission carries 3 photos
  'Budding Botanist', // 5 distinct species across both kinds
  'Palm Reader', // the Washingtonia check-in
  'Rare Find', // Sequoia, 1 in the city
  'One of One', // ...which is also the only one
  'Gentle Giant', // 62" dbh
  'Old Soul', // planted 1898
  'Branching Out', // USSFO + USNYC
]

/** Locked badges whose progress bar should read against these targets. */
const LOCKED_PROGRESS: Array<[title: string, progress: string]> = [
  ['Grove Starter', '2 / 5'], // submissions
  ['Tree Trekker', '3 / 5'], // check-ins
  ['Conifer Collector', '1 / 5'], // conifers
  ['Shape Shifter', '3 / 4'], // distinct forms
]

const SIGNED_OUT: E2EFixtures = { user: null }

const NEWCOMER: E2EFixtures = {
  user: { uid: 'e2e-empty-user', isAnonymous: true },
  submissions: [],
  checkins: [],
}

async function open(page: Page, viewport: typeof DESKTOP, path: string, fixtures: E2EFixtures) {
  await page.setViewportSize(viewport)
  await page.addInitScript((seed) => {
    localStorage.setItem('sf_trees_welcome_dismissed', '1')
    window.__treeE2E = seed as never
  }, fixtures)
  await page.goto(`/#${path}`)
}

/**
 * The seeded session landing is what proves the running bundle carries the
 * seam: a preview server left over from a plain `pnpm build` serves the
 * signed-out UI instead. Say that, rather than failing on a missing badge grid
 * twenty lines later. Call it on the profile or contributions screen.
 */
async function assertE2EBuild(page: Page) {
  await expect(
    page.locator('.achievements, .badge-strip').first(),
    'seeded session did not land — the served build may predate `pnpm build:e2e`; stop any stale `vite preview` on port 6173 and rerun',
  ).toBeVisible({ timeout: 20_000 })
}

/** X positions of the first grid row — i.e. how many columns are laid out. */
async function columnCount(page: Page): Promise<number> {
  const boxes = await page.locator('.badge').evaluateAll((els) =>
    els.map((el) => {
      const r = el.getBoundingClientRect()
      return { x: Math.round(r.x), y: Math.round(r.y) }
    }),
  )
  expect(boxes.length).toBeGreaterThan(0)
  const firstRowY = Math.min(...boxes.map((b) => b.y))
  return new Set(boxes.filter((b) => b.y === firstRowY).map((b) => b.x)).size
}

/**
 * Match on the title element only — the tile also carries the description, and
 * the title shares its element with the "New!" chip, so neither an exact
 * whole-tile match nor an exact text match finds it.
 */
function badge(page: Page, title: string) {
  return page.locator('.badge').filter({ has: page.locator('.badge-title', { hasText: title }) })
}

for (const [label, viewport] of [
  ['desktop', DESKTOP],
  ['mobile', MOBILE],
] as const) {
  test.describe(`Achievements — ${label}`, () => {
    test('the contributions page grades every badge for a contributor', async ({ page }) => {
      await open(page, viewport, '/contributions', CONTRIBUTOR)
      await assertE2EBuild(page)

      const grid = page.locator('.achievements')
      await expect(grid).toBeVisible()

      // Every defined achievement is rendered, earned or not.
      await expect(page.locator('.badge')).toHaveCount(ACHIEVEMENTS.length)
      await expect(page.locator('.achievements-summary')).toContainText(
        `${EARNED_TITLES.length} / ${ACHIEVEMENTS.length}`,
      )
      await expect(page.locator('.achievements-summary')).toContainText('badges earned')

      for (const title of EARNED_TITLES) {
        const earned = badge(page, title)
        await expect(earned, `${title} should be earned`).toHaveClass(/earned/)
        // Earned badges show their own emoji; locked ones show a padlock.
        await expect(earned.locator('.badge-emoji')).not.toHaveText('🔒')
      }

      // Anything not in the list is locked, so the count and the tiles agree.
      await expect(page.locator('.badge.earned')).toHaveCount(EARNED_TITLES.length)
    })

    test('locked badges show progress toward their target', async ({ page }) => {
      await open(page, viewport, '/contributions', CONTRIBUTOR)
      await assertE2EBuild(page)

      for (const [title, progress] of LOCKED_PROGRESS) {
        const locked = badge(page, title)
        await expect(locked, `${title} should still be locked`).not.toHaveClass(/earned/)
        await expect(locked.locator('.badge-emoji')).toHaveText('🔒')
        await expect(locked.locator('.badge-progress__label')).toHaveText(progress)
        // The bar is a fraction of the tile, not full and not empty.
        const filled = await locked.locator('.badge-progress__bar').boundingBox()
        const track = await locked.locator('.badge-progress').boundingBox()
        expect(filled!.width).toBeGreaterThan(0)
        expect(filled!.width).toBeLessThan(track!.width)
      }

      // Single-target badges have nothing to show a bar for.
      await expect(badge(page, 'Dawn Chorus').locator('.badge-progress')).toHaveCount(0)
    })

    test('earned badges sort ahead of locked ones', async ({ page }) => {
      await open(page, viewport, '/contributions', CONTRIBUTOR)
      await assertE2EBuild(page)

      const earnedFlags = await page
        .locator('.badge')
        .evaluateAll((els) => els.map((el) => el.classList.contains('earned')))
      const firstLocked = earnedFlags.indexOf(false)
      expect(firstLocked).toBe(EARNED_TITLES.length)
      expect(earnedFlags.slice(firstLocked).some(Boolean)).toBe(false)
    })

    test('"New!" chips mark first sight of a badge and clear on the next visit', async ({ page }) => {
      await open(page, viewport, '/contributions', CONTRIBUTOR)
      await assertE2EBuild(page)

      await expect(page.locator('.badge-new')).toHaveCount(EARNED_TITLES.length)
      await expect(badge(page, 'First Roots').locator('.badge-new')).toHaveText('New!')

      // The component persists what it showed, so a revisit is quiet.
      await expect
        .poll(() => page.evaluate(() => localStorage.getItem('treeAchievements.seen')))
        .not.toBeNull()

      await page.reload()
      await expect(page.locator('.achievements')).toBeVisible()
      await expect(page.locator('.badge-new')).toHaveCount(0)
    })

    test('the badge grid fits the viewport', async ({ page }) => {
      await open(page, viewport, '/contributions', CONTRIBUTOR)
      await assertE2EBuild(page)
      await expect(page.locator('.badge').first()).toBeVisible()

      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      )
      expect(overflow, 'the page scrolls sideways').toBeLessThanOrEqual(1)

      for (const box of await page
        .locator('.badge')
        .evaluateAll((els) => els.map((el) => el.getBoundingClientRect()))) {
        expect(box.left).toBeGreaterThanOrEqual(0)
        expect(box.right).toBeLessThanOrEqual(viewport.width + 1)
      }

      // The grid is responsive: a phone still gets a real grid, not one tile
      // per row, and the desktop card fits more across than the phone does.
      const columns = await columnCount(page)
      expect(columns).toBeGreaterThanOrEqual(2)
      if (label === 'desktop') expect(columns).toBeGreaterThanOrEqual(3)
    })

    test('the profile badge strip summarises and links to the grid', async ({ page }) => {
      await open(page, viewport, '/profile', CONTRIBUTOR)
      await assertE2EBuild(page)

      const strip = page.locator('.badge-strip')
      await expect(strip).toBeVisible()
      await expect(strip).toContainText(`${EARNED_TITLES.length} / ${ACHIEVEMENTS.length}`)
      await expect(strip).toContainText('badges')
      await expect(strip.locator('.badge-strip__emoji')).toHaveCount(EARNED_TITLES.length)

      // Each emoji carries its badge's name and description as a tooltip.
      await expect(strip.locator('.badge-strip__emoji').first()).toHaveAttribute(
        'title',
        /.+ — .+/,
      )

      await strip.click()
      await expect(page).toHaveURL(/#\/contributions/)
      await expect(page.locator('.achievements')).toBeVisible()
      await expect(page.locator('.badge.earned')).toHaveCount(EARNED_TITLES.length)
    })

    test('a contributor with nothing yet is nudged rather than shown an empty box', async ({ page }) => {
      await open(page, viewport, '/profile', NEWCOMER)
      await assertE2EBuild(page)

      const strip = page.locator('.badge-strip')
      await expect(strip).toContainText('No badges yet')
      await expect(strip.locator('.badge-strip__emoji')).toHaveCount(0)

      await strip.click()
      await expect(page.locator('.achievements-summary')).toContainText(
        `0 / ${ACHIEVEMENTS.length}`,
      )
      await expect(page.locator('.badge.earned')).toHaveCount(0)
      await expect(page.locator('.badge-new')).toHaveCount(0)
      await expect(badge(page, 'First Roots').locator('.badge-emoji')).toHaveText('🔒')
    })

    test('signed-out visitors are asked to sign in instead of seeing badges', async ({ page }) => {
      await open(page, viewport, '/contributions', SIGNED_OUT)

      await expect(page.locator('.contributions-status')).toContainText(
        'Sign in to see your contributions',
      )
      await expect(page.locator('.achievements')).toHaveCount(0)

      await open(page, viewport, '/profile', SIGNED_OUT)
      await expect(page.locator('.profile-status')).toContainText("You're not signed in")
      await expect(page.locator('.badge-strip')).toHaveCount(0)
    })
  })
}

test.describe('Achievements — mobile navigation', () => {
  test('the mobile menu reaches the badge grid', async ({ page }) => {
    await open(page, MOBILE, '/', CONTRIBUTOR)

    await page.getByTestId('mobile-nav-trigger').click()
    await expect(page.locator('.mobile-nav-menu')).toBeVisible()
    await page.locator('.mobile-nav-item').filter({ hasText: 'My contributions' }).click()

    await assertE2EBuild(page)
    await expect(page.locator('.mobile-full-screen')).toBeVisible()
    await expect(page.locator('.achievements')).toBeVisible()
    await expect(page.locator('.badge.earned')).toHaveCount(EARNED_TITLES.length)

    // The bottom bar keeps its own chrome above the grid rather than covering
    // the last row of badges.
    const lastBadge = page.locator('.badge').last()
    await lastBadge.scrollIntoViewIfNeeded()
    const badgeBox = (await lastBadge.boundingBox())!
    const barBox = (await page.locator('.mobile-bottom-bar').boundingBox())!
    expect(badgeBox.y).toBeLessThan(barBox.y)
  })
})
