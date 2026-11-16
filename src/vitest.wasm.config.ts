import { defineConfig } from 'vitest/config'

// The tile worker on DuckDB-WASM under Node (src/workers/*.wasm.test.ts):
// the engine the browser runs, which plans some queries differently from the
// native DuckDB the default suite uses (see src/workers/wasm/nodeDuckDB.ts).
// Its own config and CI job because each file boots the 34 MB engine and
// builds a synthetic city: about 20 s, against the default suite's seconds.
export default defineConfig({
  test: {
    environment: 'node',
    include: ['src/**/*.wasm.test.ts'],
    // One file at a time: each holds an engine and a 300k-tree world in memory.
    fileParallelism: false,
    testTimeout: 120_000,
    hookTimeout: 180_000,
  },
})
