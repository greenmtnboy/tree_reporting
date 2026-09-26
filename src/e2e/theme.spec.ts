import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('sf_trees_welcome_dismissed', '1'))
  await page.route('**/gc.zgo.at/**', route => route.fulfill({ body: '' }))
})

test('follows the system live, preserves an override, and restores system mode', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/info?city=USSFO')
  const root = page.locator('html')
  await expect(root).toHaveAttribute('data-theme', 'light')
  await expect(page.getByRole('button', { name: 'System', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.emulateMedia({ colorScheme: 'dark' })
  await expect(root).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: 'Light', exact: true }).click()
  await expect(root).toHaveAttribute('data-theme', 'light')
  await page.reload()
  await expect(root).toHaveAttribute('data-theme', 'light')
  await expect(page.getByRole('button', { name: 'Light', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'System', exact: true }).click()
  await expect(root).toHaveAttribute('data-theme', 'dark')
  await page.emulateMedia({ colorScheme: 'light' })
  await expect(root).toHaveAttribute('data-theme', 'light')
})

test('syncs preferences across tabs', async ({ page, context }) => {
  await page.goto('/#/info?city=USSFO')
  const second = await context.newPage()
  await second.goto('/#/info?city=USSFO')
  await page.getByRole('button', { name: 'Dark', exact: true }).click()
  await expect(second.locator('html')).toHaveAttribute('data-theme', 'dark')
  await second.getByRole('button', { name: 'Light', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
})

test('works when preference storage is unavailable', async ({ page }) => {
  await page.addInitScript(() => {
    const getItem = Storage.prototype.getItem
    const setItem = Storage.prototype.setItem
    Storage.prototype.getItem = function (key) {
      if (key === 'urban-trees-theme') throw new Error('Storage disabled')
      return getItem.call(this, key)
    }
    Storage.prototype.setItem = function (key, value) {
      if (key === 'urban-trees-theme') throw new Error('Storage disabled')
      setItem.call(this, key, value)
    }
  })
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/info?city=USSFO')
  const close = page.getByRole('button', { name: 'Close', exact: true })
  if (await close.isVisible()) await close.click()
  await page.getByRole('button', { name: 'Dark', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
})

test('mobile navigation exposes the same theme preference without overflowing', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/#/info?city=USSFO')
  await page.getByTestId('mobile-nav-trigger').click()
  await page.getByRole('button', { name: 'Dark', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByRole('button', { name: 'Dark', exact: true })).toHaveAttribute('aria-pressed', 'true')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})

test('background drawings follow the active city ecosystem', async ({ page }) => {
  await page.goto('/#/info?city=USSFO')
  const backdrop = page.locator('.field-backdrop')
  await expect(backdrop).toHaveAttribute('data-ecosystem', 'woodland')
  for (const [city, biome] of [
    ['USBOS', 'broadleaf'], ['CAVAN', 'conifer'], ['USTEM', 'desert'], ['ARBUE', 'grassland'],
    ['COBOG', 'tropical'], ['TWTPE', 'tropical'], ['FIHEL', 'boreal'],
    ['JPTYO', 'broadleaf'], ['DKCPH', 'broadleaf'], ['USDEN', 'grassland'], ['CAVIC', 'conifer'],
  ]) {
    await page.getByRole('combobox', { name: 'Select city' }).selectOption(city!)
    await expect(backdrop).toHaveAttribute('data-ecosystem', biome!)
  }
})

test('biome growth respects reduced motion and plays once for a new biome', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/#/info?city=USSFO')
  const plant = page.locator('.ecosystem-study .botanical')
  await expect(plant).toBeVisible()
  expect(await plant.evaluate(el => el.getAnimations().length)).toBe(0)
  await expect(plant).toHaveCSS('opacity', '1')
  await expect(plant).toHaveCSS('clip-path', 'none')

  await page.emulateMedia({ reducedMotion: 'no-preference' })
  await page.getByRole('combobox', { name: 'Select city' }).selectOption('USBOS')
  await expect(page.locator('.ecosystem-study')).toHaveAttribute('data-sketch', 'broadleaf')
  await expect.poll(() => plant.evaluate(el => el.getAnimations().some(animation => animation.playState === 'running'))).toBe(true)
  await expect.poll(() => plant.evaluate(el => el.getAnimations().every(animation => animation.playState === 'finished')), { timeout: 6_000 }).toBe(true)
  // Animated lengths can retain either percentage or pixel units at zero.
  await expect(plant).toHaveCSS('clip-path', /^inset\(0(?:%|px)?(?: 0(?:%|px)?){0,3}\)$/)
  await expect(plant).toHaveCSS('opacity', '1')
})

test('changing map theme preserves tree layers and camera', async ({ page }) => {
  test.setTimeout(120_000)
  await page.emulateMedia({ colorScheme: 'light' })
  await page.goto('/#/?city=USSFO')
  await expect(page.locator('.tree-map')).toHaveAttribute('data-trees-loaded-for', 'USSFO', { timeout: 90_000 })
  await expect(page.getByRole('button', { name: 'Look north', exact: true })).toBeEnabled({ timeout: 30_000 })
  const before = await page.evaluate(() => {
    const map = (window as any).__treeMap
    return { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), water: map.getPaintProperty('water', 'fill-color'), layers: map.getStyle().layers.map((l: { id: string }) => l.id) }
  })
  await page.getByRole('button', { name: 'Dark', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__treeMap.getPaintProperty('water', 'fill-color'))).not.toEqual(before.water)
  const after = await page.evaluate(() => {
    const map = (window as any).__treeMap
    return { center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), layers: map.getStyle().layers.map((l: { id: string }) => l.id), trees: !!map.getSource('trees') }
  })
  expect(after.trees).toBe(true)
  expect(after.center).toEqual(before.center)
  expect(after.zoom).toBeCloseTo(before.zoom, 5)
  expect(after.bearing).toBeCloseTo(before.bearing, 5)
  expect(after.layers).toEqual(before.layers)
  await page.getByRole('button', { name: 'Light', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__treeMap.getPaintProperty('water', 'fill-color'))).toEqual(before.water)
})
