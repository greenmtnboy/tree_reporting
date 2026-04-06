# Map Loading State — Current Flow & Simplification

## Problem

When a user switches cities during the initial intro animation, the map
populates with the correct data but the chat assistant panel never unlocks.
The root cause is **scattered boolean flags** across multiple composables with
no single source of truth and no coordinated reset on city switch.

## Current Flags (Before)

| Flag | Location | Purpose | Problem |
|------|----------|---------|---------|
| `ready` | `useDuckDBClient.ts` | DuckDB has city data loaded | Never reset to false on city switch |
| `introComplete` | `useMapIntro.ts` | Intro animation finished | Singleton ref, never reset for new cities |
| `defaultQueryLoading` | `TreeMap.vue` (local) | Loading overlay visible | Set true/false by multiple async paths |
| `introActive` | `TreeMap.vue` (local) | Intro animation is running | Managed by animation composable |
| `citySwitchInProgress` | `TreeMap.vue` (local) | Prevents concurrent switches | Silently drops switch if already switching |
| `introStarted` | `useMapIntroAnimation.ts` | Prevents re-running intro | Never reset — second city's intro never runs |
| `introCancelled` | `useMapIntroAnimation.ts` | Cancels running intro RAF | Module-level, not reset per city |

The chat panel's `inputDisabled` computed combines **three** of these via:
```ts
isLoading.value || !activeDataReady.value || (isMapScreen.value && !introComplete.value)
```

When a city switch interrupts the intro, the finally-block timing of
`setIntroComplete()` and the `introStarted` guard interact to leave the
flags in an inconsistent state.

## Simplification: Single State Machine

Replace the scattered flags with a single `useMapLifecycle` composable that
tracks the map's loading phase as a state machine:

```
  ┌──────────────┐
  │  initializing │ ─── DuckDB + tile protocol registering
  └──────┬───────┘
         │ ensureTileProtocolRegistered resolves
         ▼
  ┌──────────────┐
  │   loading     │ ─── setCityContext running, tiles being generated
  └──────┬───────┘
         │ trees sourcedata fires (tiles loaded)
    ┌────┴────┐
    │         │
    ▼         ▼
┌────────┐ ┌───────┐
│ intro  │ │ ready │  ← mobile/simplified skips intro
└───┬────┘ └───────┘
    │ animation done    ▲
    └───────────────────┘
         ▲         │
         │         ▼
      ┌──┴───────────────┐
      │    switching      │ ─── globe swoop + new city load
      └──────────────────┘

Any state → switching  (user picks a new city)
switching → ready      (new city's tiles loaded)
```

### Key Properties

- `phase`: the current state
- `chatReady`: `phase === 'ready'` — single boolean the chat panel checks
- `showLoadingOverlay`: `phase !== 'ready'`
- State transitions are explicit function calls from TreeMap.vue

### Why This Fixes the Bug

There is exactly **one** piece of state. A city switch always transitions to
`switching`, and only transitions to `ready` when the new city's tiles have
loaded. No combination of cancelled animations or timing races can leave the
machine in a state where the chat thinks data is ready but the intro hasn't
completed, because those are no longer separate flags.
