import TREE_ENRICHMENT_MODEL from '../../data/raw/tree_enrichment.preql?raw'
import TREE_INFO_MODEL from '../../data/raw/tree_info.preql?raw'
import TREE_COMMON_MODEL from '../../data/raw/tree_common.preql?raw'
import CORE_MODEL from '../../data/raw/core.preql?raw'
import SF_TREE_INFO_MODEL from '../../data/raw/sf/sf_tree_info.preql?raw'
import NYC_TREE_INFO_MODEL from '../../data/raw/nyc/nyc_tree_info.preql?raw'
import BOSTON_TREE_INFO_MODEL from '../../data/raw/boston/boston_tree_info.preql?raw'

export const ALL_MODEL_SOURCES = [
  { alias: 'tree_enrichment', contents: TREE_ENRICHMENT_MODEL },
  { alias: 'tree_info', contents: TREE_INFO_MODEL },
  { alias: 'tree_common', contents: TREE_COMMON_MODEL },
  { alias: 'core', contents: CORE_MODEL },
  { alias: 'sf_tree_info', contents: SF_TREE_INFO_MODEL },
  { alias: 'nyc_tree_info', contents: NYC_TREE_INFO_MODEL },
  { alias: 'boston_tree_info', contents: BOSTON_TREE_INFO_MODEL },
]
