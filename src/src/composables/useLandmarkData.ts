import { ref, watch } from 'vue'
import type { Landmark } from '../types'
import { useDuckDB } from './useDuckDB'
import { useMapData } from './useMapData'

export function useLandmarkData() {
  const landmarks = ref<Landmark[]>([])
  const loading = ref(true)
  const error = ref<string | null>(null)

  const { query } = useDuckDB()
  const { selectedCity } = useMapData()

  async function load(city: string) {
    loading.value = true
    try {
      const result = await query(`
        SELECT landmark_id, name, latitude, longitude
        FROM landmarks
        WHERE city = '${city}'
        ORDER BY name
      `)
      landmarks.value = result.rows
        .filter((r) => r.name)
        .map((r) => ({
          id: (r.landmark_id as string) ?? '',
          name: (r.name as string).trim(),
          lng: r.longitude as number,
          lat: r.latitude as number,
        }))
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  watch(selectedCity, (city) => { void load(city) }, { immediate: true })

  return { landmarks, loading, error }
}
