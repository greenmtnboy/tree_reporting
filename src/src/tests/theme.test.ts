import { describe, expect, it } from 'vitest'
import type { Map } from 'maplibre-gl'
import { storedPreference } from '../composables/useTheme'
import { applyLandTint } from '../composables/mapTheme'

const storage = (values: Record<string, string>) => ({ getItem: (key: string) => values[key] ?? null })

describe('storedPreference', () => {
  it("prefers the explorer's own choice", () => {
    expect(storedPreference(storage({ 'urban-trees-theme': 'light', 'vitepress-theme-appearance': 'dark' }))).toBe('light')
  })
  it("follows arborary.world's toggle when the explorer has none", () => {
    expect(storedPreference(storage({ 'vitepress-theme-appearance': 'dark' }))).toBe('dark')
    expect(storedPreference(storage({ 'vitepress-theme-appearance': 'auto' }))).toBe('system')
  })
  it('defaults to the system setting', () => {
    expect(storedPreference(storage({}))).toBe('system')
  })
})

describe('applyLandTint', () => {
  function fakeMap(layers: Record<string, string>) {
    const paints: Array<[string, string, unknown]> = []
    const map = {
      getLayer: (id: string) => (layers[id] ? { id, type: layers[id] } : undefined),
      setPaintProperty: (id: string, name: string, value: unknown) => { paints.push([id, name, value]) },
    } as unknown as Map
    return { map, paints }
  }

  it('tints the background and land fills in dark', () => {
    const { map, paints } = fakeMap({ background: 'background', landcover: 'fill', landuse: 'fill', water: 'fill' })
    applyLandTint(map, 'dark')
    expect(paints.map(([id]) => id)).toEqual(['background', 'landcover', 'landuse'])
  })

  it('touches only the background in light and skips missing layers', () => {
    const { map, paints } = fakeMap({ background: 'background', landcover: 'fill' })
    applyLandTint(map, 'light')
    expect(paints.map(([id]) => id)).toEqual(['background'])
    const empty = fakeMap({})
    applyLandTint(empty.map, 'dark')
    expect(empty.paints).toEqual([])
  })
})
