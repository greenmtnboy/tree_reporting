import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

const trilogyStudioLibRoot = resolve(__dirname, '../../trilogy-studio-core/lib')

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
  base: process.env.NODE_ENV === 'production' ? '/sf_tree_reporting/' : '/',
  plugins: [vue(), stubMotherDuck],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@trilogy-data/trilogy-studio-components/dashboard': resolve(
        trilogyStudioLibRoot,
        'entry.dashboard.ts',
      ),
      '@trilogy-data/trilogy-studio-components/llm': resolve(trilogyStudioLibRoot, 'entry.llm.ts'),
      '@trilogy-data/trilogy-studio-components/stores': resolve(
        trilogyStudioLibRoot,
        'entry.stores.ts',
      ),
      '@trilogy-data/trilogy-studio-components/connections': resolve(
        trilogyStudioLibRoot,
        'entry.connections.ts',
      ),
      '@trilogy-data/trilogy-studio-components/style.css': resolve(
        trilogyStudioLibRoot,
        'embedTheme.css',
      ),
    },
    dedupe: ['vue', 'pinia'],
  },
  server: {
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
