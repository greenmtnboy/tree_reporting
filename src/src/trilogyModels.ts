import TREE_ENRICHMENT_MODEL from '../../data/raw/tree_enrichment.preql?raw'
import TREE_INFO_MODEL from '../../data/raw/tree_info.preql?raw'
import TREE_COMMON_MODEL from '../../data/raw/tree_common.preql?raw'
import CORE_MODEL from '../../data/raw/core.preql?raw'
import SF_TREE_INFO_MODEL from '../../data/raw/sf/sf_tree_info.preql?raw'
import NYC_TREE_INFO_MODEL from '../../data/raw/nyc/nyc_tree_info.preql?raw'
import BOSTON_TREE_INFO_MODEL from '../../data/raw/boston/boston_tree_info.preql?raw'
import PARIS_TREE_INFO_MODEL from '../../data/raw/paris/paris_tree_info.preql?raw'
import LANDMARK_COMMON_MODEL from '../../data/raw/landmark_common.preql?raw'
import LANDMARK_INFO_MODEL from '../../data/raw/landmark_info.preql?raw'
import SF_LANDMARKS_MODEL from '../../data/raw/sf/sf_landmarks.preql?raw'
import NYC_LANDMARKS_MODEL from '../../data/raw/nyc/nyc_landmarks.preql?raw'
import BOSTON_LANDMARKS_MODEL from '../../data/raw/boston/boston_landmarks.preql?raw'
import PARIS_LANDMARKS_MODEL from '../../data/raw/paris/paris_landmarks.preql?raw'
import BURLINGTON_TREE_INFO_MODEL from '../../data/raw/burlington/burlington_tree_info.preql?raw'
import BURLINGTON_LANDMARKS_MODEL from '../../data/raw/burlington/burlington_landmarks.preql?raw'

export const ALL_MODEL_SOURCES = [
  { alias: 'tree_enrichment', contents: TREE_ENRICHMENT_MODEL },
  { alias: 'tree_info', contents: TREE_INFO_MODEL },
  { alias: 'tree_common', contents: TREE_COMMON_MODEL },
  { alias: 'core', contents: CORE_MODEL },
  { alias: 'sf.sf_tree_info', contents: SF_TREE_INFO_MODEL },
  { alias: 'nyc.nyc_tree_info', contents: NYC_TREE_INFO_MODEL },
  { alias: 'boston.boston_tree_info', contents: BOSTON_TREE_INFO_MODEL },
  { alias: 'paris.paris_tree_info', contents: PARIS_TREE_INFO_MODEL },
  { alias: 'landmark_common', contents: LANDMARK_COMMON_MODEL },
  { alias: 'landmark_info', contents: LANDMARK_INFO_MODEL },
  { alias: 'sf.sf_landmarks', contents: SF_LANDMARKS_MODEL },
  { alias: 'nyc.nyc_landmarks', contents: NYC_LANDMARKS_MODEL },
  { alias: 'boston.boston_landmarks', contents: BOSTON_LANDMARKS_MODEL },
  { alias: 'paris.paris_landmarks', contents: PARIS_LANDMARKS_MODEL },
  { alias: 'burlington.burlington_tree_info', contents: BURLINGTON_TREE_INFO_MODEL },
  { alias: 'burlington.burlington_landmarks', contents: BURLINGTON_LANDMARKS_MODEL },
]
