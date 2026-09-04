import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // dashboard-queries runs against the hosted resolver, so it has its own
    // config and CI job (vitest.queries.config.ts) — network, not runtime.
    exclude: ['**/node_modules/**', '**/e2e/**', '**/dashboard-queries.test.ts'],
  },
})
