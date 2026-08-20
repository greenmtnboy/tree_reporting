import { defineConfig, devices } from '@playwright/test'

const PORT = 6173

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 1 : undefined,
  globalTimeout: 15 * 60 * 1000,

  reporter: process.env.CI
    ? [
        ['list'],
        ['github'],
        ['html', { outputFolder: 'playwright-report', open: 'never' }],
      ]
    : [
        ['list'],
        ['html', { open: 'on-failure' }],
      ],

  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: process.env.CI ? 'retain-on-failure' : 'on-first-retry',
    screenshot: 'only-on-failure',
    video: process.env.CI ? 'retain-on-failure' : 'off',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  webServer: {
    // Builds in e2e mode so the auth/contribution fixture seam is compiled in
    // (see src/lib/e2eFixtures.ts). A plain `pnpm build` dist cannot drive the
    // achievement specs, so the build is part of starting the server rather
    // than a step everyone has to remember.
    command: `pnpm build:e2e && pnpm preview --port ${PORT}`,
    port: PORT,
    reuseExistingServer: !process.env.CI,
    timeout: 300_000,
  },
})