import { computed, readonly, ref } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ColorTheme = 'light' | 'dark'
const STORAGE_KEY = 'urban-trees-theme'
/** arborary.world (VitePress) serves this app at /explore/ on the same origin
 * and stores its toggle here as 'light' | 'dark' | 'auto'. Until the explorer
 * has a choice of its own, follow the site's so the two never disagree. */
const SITE_STORAGE_KEY = 'vitepress-theme-appearance'
const preference = ref<ThemePreference>('system')
const systemDark = ref(false)
const resolvedTheme = computed<ColorTheme>(() =>
  preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value,
)

function readPreference(value: string | null): ThemePreference {
  return value === 'light' || value === 'dark' ? value : 'system'
}

/** The explorer's own saved choice, else the arborary.world site's. */
export function storedPreference(storage: Pick<Storage, 'getItem'>): ThemePreference {
  return readPreference(storage.getItem(STORAGE_KEY) ?? storage.getItem(SITE_STORAGE_KEY))
}

function applyTheme() {
  document.documentElement.dataset.theme = resolvedTheme.value
  document.documentElement.style.colorScheme = resolvedTheme.value
}

/** Initialize once at app startup; storage may be disabled in private browsing. */
export function initializeTheme() {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  systemDark.value = query.matches
  const load = () => {
    try { preference.value = storedPreference(localStorage) } catch { /* Use system. */ }
    applyTheme()
  }
  load()
  const onSystemChange = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches
    applyTheme()
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY && event.key !== SITE_STORAGE_KEY && event.key !== null) return
    load()
  }
  query.addEventListener('change', onSystemChange)
  window.addEventListener('storage', onStorage)
  return () => {
    query.removeEventListener('change', onSystemChange)
    window.removeEventListener('storage', onStorage)
  }
}

export function useTheme() {
  function setPreference(value: ThemePreference) {
    preference.value = value
    try { localStorage.setItem(STORAGE_KEY, value) } catch { /* Keep the in-memory choice. */ }
    applyTheme()
  }
  return { preference: readonly(preference), resolvedTheme, setPreference }
}
