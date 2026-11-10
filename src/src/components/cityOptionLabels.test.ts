import { describe, expect, it } from 'vitest'
import cityConfig from '../cityConfig.json'
import { cityOptionLabels } from './cityOptionLabels'

describe('cityOptionLabels', () => {
  const labels = cityOptionLabels(cityConfig)

  it('labels every city "City, Country"', () => {
    expect(labels.USSFO).toBe('San Francisco, United States')
    expect(labels.FRPAR).toBe('Paris, France')
    expect(labels.COBOG).toBe('Bogotá, Colombia')
    expect(labels.TWTPE).toBe('Taipei, Taiwan')
    expect(labels.CZPRG).toBe('Prague, Czechia')
  })

  it('keeps the state only where two cities share a name', () => {
    expect(labels.USBTV).toBe('Burlington, VT, United States')
    expect(labels.CABUR).toBe('Burlington, ON, Canada')
    expect(labels.USSTO).toBe('Stoughton, United States')
  })

  it('keeps DC as part of Washington\'s name', () => {
    expect(labels.USWAS).toBe('Washington, DC, United States')
  })

  it('gives every city a country and a distinct label', () => {
    for (const [code, label] of Object.entries(labels)) {
      expect(label.split(', ').length, `${code}: ${label}`).toBeGreaterThanOrEqual(2)
    }
    expect(new Set(Object.values(labels)).size).toBe(Object.keys(labels).length)
  })
})
