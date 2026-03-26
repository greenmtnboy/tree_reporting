import { useTrilogyCore } from '@trilogy-data/trilogy-studio-components/dashboard'

export const TRILOGY_RESOLVER_URL = 'https://trilogy-service.fly.dev'

export function useTrilogyRuntime() {
  const trilogy = useTrilogyCore()

  if (
    !trilogy.userSettingsStore.settings.trilogyResolver ||
    trilogy.userSettingsStore.settings.trilogyResolver.includes('localhost')
  ) {
    trilogy.userSettingsStore.updateSetting('trilogyResolver', TRILOGY_RESOLVER_URL)
  }

  return trilogy
}
