import { useTrilogyCore } from '@trilogy-data/trilogy-studio-components/dashboard'

// A build-time override points a dev server at a local resolver (the
// trilogy-studio-core pyserver), which is how a planner fix is verified
// end-to-end before the hosted one is bumped to it.
const RESOLVER_OVERRIDE = import.meta.env.VITE_TRILOGY_RESOLVER_URL as string | undefined
export const TRILOGY_RESOLVER_URL = RESOLVER_OVERRIDE || 'https://trilogy-service.fly.dev'

export function useTrilogyRuntime() {
  const trilogy = useTrilogyCore()

  const current = trilogy.userSettingsStore.settings.trilogyResolver
  if (!current || current.includes('localhost') || RESOLVER_OVERRIDE) {
    trilogy.userSettingsStore.updateSetting('trilogyResolver', TRILOGY_RESOLVER_URL)
  }

  return trilogy
}
