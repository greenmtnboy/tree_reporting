import type { FieldSketchName } from './field-sketches'

export type BiomeSketchName = Exclude<FieldSketchName, 'city'>

// Exact RESOLVE labels from the city context. Keep coverage explicit: a new
// city biome must get an intentional artwork choice, not a substring match.
export const biomeSketches = {
  'Mediterranean Forests, Woodlands & Scrub': 'woodland',
  'Temperate Broadleaf & Mixed Forests': 'broadleaf',
  'Temperate Conifer Forests': 'conifer',
  'Deserts & Xeric Shrublands': 'desert',
  'Temperate Grasslands, Savannas & Shrublands': 'grassland',
  'Tropical & Subtropical Moist Broadleaf Forests': 'tropical',
  'Boreal Forests/Taiga': 'boreal',
  'Mangroves': 'mangrove',
  'Tropical & Subtropical Dry Broadleaf Forests': 'dry-forest',
  'Flooded Grasslands & Savannas': 'wetland',
  'Montane Grasslands & Shrublands': 'montane',
  'Tundra': 'tundra',
  'Tropical & Subtropical Coniferous Forests': 'conifer',
  'Tropical & Subtropical Grasslands, Savannas & Shrublands': 'grassland',
} as const satisfies Record<string, BiomeSketchName>

/** The all-cities view (and unknown future contexts) retains the neutral oak study. */
export function getBiomeSketch(biome: string): BiomeSketchName {
  return Object.prototype.hasOwnProperty.call(biomeSketches, biome)
    ? biomeSketches[biome as keyof typeof biomeSketches]
    : 'broadleaf'
}
