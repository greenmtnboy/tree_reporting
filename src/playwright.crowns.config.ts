import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './renderer-tests',
  workers: 1,
  timeout: 60_000,
  use: { baseURL: 'http://localhost:6174', viewport: { width: 1100, height: 800 } },
  reporter: 'list',
  webServer: {
    command: 'pnpm dev --port 6174', port: 6174,
    reuseExistingServer: !process.env.CI,
  },
})
