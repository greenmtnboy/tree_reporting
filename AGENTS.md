# Project Context


## Tech

Vite, vue, typescript

Use pnpm not NPM for all management. 

This is critical - NO NPM.

## Testing

From `src/`: `pnpm test` (vitest), `pnpm test:e2e` (Playwright), `pnpm lint`.

Playwright's webServer runs `pnpm build:e2e` — a `--mode e2e` build that loads
`src/.env.e2e` and compiles in the fixture seam in `src/src/lib/e2eFixtures.ts`.
The seam lets specs seed an auth session and contribution history through
`window.__treeE2E` (via `page.addInitScript`), which is the only way to reach
the achievement/badge UI without a live Firebase. `import.meta.env.VITE_E2E` is
statically replaced, so a normal `pnpm build` contains none of it — verify with
`grep -c __treeE2E dist/assets/index-*.js` after building.

Desktop and mobile coverage is expressed as viewport-parametrised describes in
one spec file (`for (const [label, viewport] of [...])`), not as separate
Playwright projects — see `e2e/achievements.spec.ts` and `e2e/tree-card.spec.ts`.

