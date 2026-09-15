import { defineConfig } from 'vitest/config'

// The agent chat benchmark: every suggested prompt, N rounds each, through the
// real chat loop against the live resolver and the demo model. Its own config
// because it needs the network and spends the per-IP demo budget; it is never
// part of `pnpm test`. See the header of src/bench/chat-benchmark.test.ts.
export default defineConfig({
  test: {
    environment: 'happy-dom',
    include: ['src/bench/chat-benchmark.test.ts'],
    testTimeout: 3_600_000,
    hookTimeout: 300_000,
    // Rounds are sequential and slow; the per-round lines are the only live
    // signal, so let them through instead of holding them until the end.
    disableConsoleIntercept: true,
    fileParallelism: false,
  },
})
