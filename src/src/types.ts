export interface RawTree {
  tree_id: string
  tree_name: string
  plant_date: string
  species: string
  latitude: number
  longitude: number
  diameter_at_breast_height: number | null
}

export type TreeForm =
  | 'broadleaf'
  | 'conifer'
  | 'palm'
  | 'columnar'
  | 'ornamental'
  | 'spreading'
  | 'weeping'
  | 'multi_trunk'
  | 'default'

export interface RawLandmark {
  name: string
  latitude: number
  longitude: number
}

export interface Landmark {
  id: string
  name: string
  lng: number
  lat: number
}

// Maps hex color → display label for the map legend
export type ColorLabelMap = Record<string, string>

// --- Chat types ---

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  toolCalls?: ToolCallRecord[]
  isLoading?: boolean
}

export interface ToolCallRecord {
  id: string
  name: string
  input: Record<string, unknown>
  result: string
  isError?: boolean
}
