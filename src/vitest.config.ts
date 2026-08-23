import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts'],
    // dashboard-queries runs against the hosted resolver and takes tens of
    // minutes; it has its own config and CI job (vitest.queries.config.ts).
    exclude: ['**/node_modules/**', '**/e2e/**', '**/dashboard-queries.test.ts'],
  },
})
