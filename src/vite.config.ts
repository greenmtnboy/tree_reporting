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

export default defineConfig(({ mode }) => ({
  base: mode === 'ios' ? '/' : appBase(),
  plugins: [vue(), stubMotherDuck, {
    name: 'website-head',
    transformIndexHtml() {
      if (mode === 'ios') return []
      return [
        { tag: 'link', attrs: { rel: 'icon', type: 'image/svg+xml', href: appBase() + 'favicon.svg' }, injectTo: 'head' as const },
        { tag: 'script', attrs: { 'data-goatcounter': 'https://sf-tree-reporting.goatcounter.com/count', async: true, src: 'https://gc.zgo.at/count.js' }, injectTo: 'head' as const },
      ]
    },
  }],
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
    outDir: mode === 'ios' ? 'dist-ios' : 'dist',
    // public/data and local-trees are developer fixtures, never phone data.
    copyPublicDir: mode !== 'ios',
    rollupOptions: {
      external: ['@motherduck/wasm-client'],
    },
  },
  test: {
    environment: 'node',
    include: ['src/tests/**/*.test.ts'],
  },
}))
