import { describe, expect, it } from 'vitest'
import cityConfig from '../cityConfig.json'
import { getCityBiome } from '../composables/dashboardContextSource'
import { biomeSketches, getBiomeSketch } from '../artwork/biomeSketch'
import { fieldSketches } from '../artwork/field-sketches'

describe('city artwork coverage', () => {
  it.each(Object.keys(cityConfig))('gives %s an explicit, registered biome drawing', city => {
    const biome = getCityBiome(city)
    // Do not let the all-cities fallback silently cover a newly added biome.
    expect(Object.prototype.hasOwnProperty.call(biomeSketches, biome), `${city}: ${biome}`).toBe(true)
    const drawing = fieldSketches[getBiomeSketch(biome)]
    expect(drawing.svg).toContain('viewBox="0 0 460 510"')
    expect(drawing.svg).toContain('class="botanical"')
  })

  it.each([
    ['COBOG', 'tropical'], ['TWTPE', 'tropical'], ['FIHEL', 'boreal'],
    ['JPTYO', 'broadleaf'], ['DKCPH', 'broadleaf'],
    ['USDEN', 'grassland'], ['CAWPG', 'grassland'],
    ['CAVIC', 'conifer'], ['CAKEL', 'conifer'],
  ])('selects the intended study for %s', (city, sketch) => {
    expect(getBiomeSketch(getCityBiome(city))).toBe(sketch)
  })

  it('keeps the all-cities and unknown contexts safe', () => {
    expect(getBiomeSketch(getCityBiome(null))).toBe('broadleaf')
    expect(getBiomeSketch('Unrecognized biome')).toBe('broadleaf')
    expect(getBiomeSketch('toString')).toBe('broadleaf')
  })
})
