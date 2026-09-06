import TREE_ENRICHMENT_MODEL from '../../data/raw/tree_enrichment.preql?raw'
import TREE_INFO_MODEL from '../../data/raw/tree_info.preql?raw'
import TREE_COMMON_MODEL from '../../data/raw/tree_common.preql?raw'
import CORE_MODEL from '../../data/raw/core.preql?raw'
import ECOREGION_COMMON_MODEL from '../../data/raw/ecoregion_common.preql?raw'
import ECOREGION_INFO_MODEL from '../../data/raw/ecoregion_info.preql?raw'
import LANDMARK_COMMON_MODEL from '../../data/raw/landmark_common.preql?raw'
import LANDMARK_INFO_MODEL from '../../data/raw/landmark_info.preql?raw'
// Imported by every city tree model, so the resolver needs it even though it
// is not a per-city file the glob below would pick up.
import COMMUNITY_TREE_INFO_MODEL from '../../data/raw/community_tree_info.preql?raw'
import TREE_DEDUP_MODEL from '../../data/raw/tree_dedup.preql?raw'

// Auto-discover all per-city preql files — no changes needed here when adding a city.
// Matches data/raw/{city}/{city}_tree_info.preql and data/raw/{city}/{city}_landmarks.preql
const cityTreeModels = import.meta.glob('../../data/raw/*/*_tree_info.preql', { eager: true, query: '?raw' })
const cityLandmarkModels = import.meta.glob('../../data/raw/*/*_landmarks.preql', { eager: true, query: '?raw' })

function pathToAlias(path: string): string {
  // '../../data/raw/burlington/burlington_tree_info.preql' → 'burlington.burlington_tree_info'
  const parts = path.split('/')
  const dir = parts[parts.length - 2]
  const file = parts[parts.length - 1].replace('.preql', '')
  return `${dir}.${file}`
}

export const ALL_MODEL_SOURCES = [
  { alias: 'tree_enrichment', contents: TREE_ENRICHMENT_MODEL },
  { alias: 'tree_info', contents: TREE_INFO_MODEL },
  { alias: 'tree_common', contents: TREE_COMMON_MODEL },
  { alias: 'core', contents: CORE_MODEL },
  { alias: 'ecoregion_common', contents: ECOREGION_COMMON_MODEL },
  { alias: 'ecoregion_info', contents: ECOREGION_INFO_MODEL },
  { alias: 'landmark_common', contents: LANDMARK_COMMON_MODEL },
  { alias: 'landmark_info', contents: LANDMARK_INFO_MODEL },
  { alias: 'community_tree_info', contents: COMMUNITY_TREE_INFO_MODEL },
  { alias: 'tree_dedup', contents: TREE_DEDUP_MODEL },
  ...[...Object.entries(cityTreeModels), ...Object.entries(cityLandmarkModels)].map(
    ([path, mod]) => ({ alias: pathToAlias(path), contents: (mod as { default: string }).default })
  ),
]
