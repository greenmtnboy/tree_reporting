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

export default defineConfig({
  base: process.env.GITHUB_PAGES === 'true' ? '/sf_tree_reporting/' : '/',
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
