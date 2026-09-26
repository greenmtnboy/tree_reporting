import { computed, readonly, ref } from 'vue'

export type ThemePreference = 'system' | 'light' | 'dark'
export type ColorTheme = 'light' | 'dark'
const STORAGE_KEY = 'urban-trees-theme'
const preference = ref<ThemePreference>('system')
const systemDark = ref(false)
const resolvedTheme = computed<ColorTheme>(() =>
  preference.value === 'system' ? (systemDark.value ? 'dark' : 'light') : preference.value,
)

function readPreference(value: string | null): ThemePreference {
  return value === 'light' || value === 'dark' ? value : 'system'
}

function applyTheme() {
  document.documentElement.dataset.theme = resolvedTheme.value
  document.documentElement.style.colorScheme = resolvedTheme.value
}

/** Initialize once at app startup; storage may be disabled in private browsing. */
export function initializeTheme() {
  const query = window.matchMedia('(prefers-color-scheme: dark)')
  systemDark.value = query.matches
  try { preference.value = readPreference(localStorage.getItem(STORAGE_KEY)) } catch { /* Use system. */ }
  applyTheme()
  const onSystemChange = (event: MediaQueryListEvent) => {
    systemDark.value = event.matches
    applyTheme()
  }
  const onStorage = (event: StorageEvent) => {
    if (event.key !== STORAGE_KEY && event.key !== null) return
    preference.value = readPreference(event.newValue)
    applyTheme()
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
