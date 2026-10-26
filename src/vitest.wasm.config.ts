import { defineConfig } from 'vitest/config'

// The tile worker on DuckDB-WASM under Node (src/workers/*.wasm.test.ts):
// the engine the browser runs, which plans some queries differently from the
// native DuckDB the default suite uses. Its own config and CI job because it
// needs the network: the spatial extension is fetched from
// extensions.duckdb.org on first LOAD (see src/workers/wasm/nodeDuckDB.ts).
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.wasm.test.ts'],
    testTimeout: 120_000,
    hookTimeout: 180_000,
  },
})
