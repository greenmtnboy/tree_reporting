import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

const stubMotherDuck = {
  name: 'stub-motherduck',
  resolveId(id: string) {
    if (id === '@motherduck/wasm-client') return '\0virtual:motherduck'
  },
  load(id: string) {
    if (id === '\0virtual:motherduck') return 'export const MDConnection = undefined'
  },
}

// Where the app is served from. GitHub Pages hosts it at /tree_reporting/;
// arborary.world hosts the same build at /explore/ (APP_BASE=/explore/, see
// deploy.yml). The router uses hash history, so any static subpath works.
function appBase(): string {
  const base = process.env.APP_BASE
  if (base) return base.endsWith('/') ? base : `${base}/`
  return process.env.GITHUB_PAGES === 'true' ? '/tree_reporting/' : '/'
}

export default defineConfig({
  base: appBase(),
  plugins: [vue(), stubMotherDuck],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
    dedupe: ['vue', 'pinia'],
  },
  server: {
    port: 6173,
    strictPort: true,
    sourcemapIgnoreList: (sourcePath) => sourcePath.includes('@duckdb/duckdb-wasm'),
  },
  optimizeDeps: {
    exclude: ['@duckdb/duckdb-wasm', '@motherduck/wasm-client'],
  },
  build: {
    rollupOptions: {
      external: ['@motherduck/wasm-client'],
    },
  },
  test: {
    environment: 'node',
    include: ['src/tests/**/*.test.ts'],
  },
})
