const PLANTNET_API_KEY = import.meta.env.VITE_PLANTNET_API_KEY ?? ''
const PLANTNET_ENDPOINT = 'https://my-api.plantnet.org/v2/identify/all'

export const plantnetAvailable = Boolean(PLANTNET_API_KEY)

export interface SpeciesCandidate {
  scientificName: string
  commonName: string | null
  score: number
}

interface PlantnetSpecies {
  scientificNameWithoutAuthor?: string
  commonNames?: string[]
}

interface PlantnetResult {
  score: number
  species?: PlantnetSpecies
}

interface PlantnetResponse {
  results?: PlantnetResult[]
}

export type PlantnetOrgan = 'auto' | 'leaf' | 'flower' | 'fruit' | 'bark' | 'habit'

export const PLANTNET_MAX_IMAGES = 5

export async function identifySpecies(
  photos: Blob | Blob[],
  options: { maxResults?: number; organs?: PlantnetOrgan[]; defaultOrgan?: PlantnetOrgan } = {},
): Promise<SpeciesCandidate[]> {
  if (!PLANTNET_API_KEY) {
    throw new Error('Pl@ntNet API key not configured (VITE_PLANTNET_API_KEY)')
  }
  const blobs = Array.isArray(photos) ? photos : [photos]
  if (blobs.length === 0) {
    throw new Error('At least one photo is required')
  }
  const trimmed = blobs.slice(0, PLANTNET_MAX_IMAGES)
  const { maxResults = 5, defaultOrgan = 'auto' } = options
  const organs = options.organs ?? trimmed.map(() => defaultOrgan)
  if (organs.length !== trimmed.length) {
    throw new Error('organs must align 1:1 with photos')
  }
  const url = `${PLANTNET_ENDPOINT}?api-key=${encodeURIComponent(PLANTNET_API_KEY)}`

  const form = new FormData()
  trimmed.forEach((blob, i) => {
    form.append('images', blob, `photo-${i}.jpg`)
    form.append('organs', organs[i])
  })

  const res = await fetch(url, { method: 'POST', body: form })
  if (!res.ok) {
    const detail = await res.text().catch(() => '')
    throw new Error(`Pl@ntNet ${res.status}: ${detail || res.statusText}`)
  }

  const data = (await res.json()) as PlantnetResponse
  const out: SpeciesCandidate[] = []
  for (const r of data.results ?? []) {
    const sci = r.species?.scientificNameWithoutAuthor?.trim()
    if (!sci) continue
    out.push({
      scientificName: sci,
      commonName: r.species?.commonNames?.[0] ?? null,
      score: r.score,
    })
    if (out.length >= maxResults) break
  }
  return out
}
