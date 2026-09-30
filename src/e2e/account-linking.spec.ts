import { test, expect } from '@playwright/test'

for (const [name, viewport] of Object.entries({
  mobile: { width: 390, height: 844 }, desktop: { width: 1280, height: 800 },
})) {
  test(`Google account collision is visible and preserves the guest session — ${name}`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.addInitScript(() => {
      localStorage.setItem('sf_trees_welcome_dismissed', '1')
      window.__treeE2E = {
        user: { uid: 'guest-with-work', isAnonymous: true },
        googleLinkError: 'auth/credential-already-in-use',
        checkins: [{ treeId: 'visited-tree', city: 'USBOS' }],
      }
    })
    await page.goto('/#/profile')
    await expect(page.locator('.badge-strip')).toContainText('1 / 28')
    await page.getByRole('button', { name: 'Link Google account' }).click()
    const alert = page.getByRole('alert')
    await expect(alert).toContainText('Account linking failed')
    await expect(alert).toContainText('Nothing was merged')
    await expect(alert).toContainText('contributions inaccessible')
    await expect(alert).toBeInViewport()
    await expect(alert).toBeFocused()
    await expect(page.getByText('Signed in anonymously.', { exact: true })).toBeVisible()
    await expect(page.getByText('guest-with-work', { exact: true })).toBeVisible()
    await expect(page.locator('.badge-strip')).toContainText('1 / 28')
  })
}
