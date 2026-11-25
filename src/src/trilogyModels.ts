// The preql files the hosted resolver may import, sent with every dashboard
// and chat request: the app's *consumption* models, which read published
// parquets, and nothing that builds one.
//
// No city build model is here. `tree_info.preql` is written from them by
// data/raw/tools/sync_app_models.py: one root partial datasource per city's
// published parquet with that city's `complete where`, plus the cross-city
// rollup, and no raw partitions, dedup, position or height derivations, merges
// or watermarks. The resolver parses every source it is sent on every request,
// so this list is a per-request cost; data/raw/tests/test_app_models.py checks
// that tree_info.preql reads only published files, and
// `tests/trilogyModels.test.ts` that every import below resolves.
import TREE_ENRICHMENT_MODEL from '../../data/raw/tree_enrichment.preql?raw'
import TREE_INFO_MODEL from '../../data/raw/tree_info.preql?raw'
import TREE_COMMON_MODEL from '../../data/raw/tree_common.preql?raw'
// The columns a published tree parquet carries that tree_common does not
// declare (`data_source`, LIDAR heights); see data/raw/tree_published_columns.preql.
import TREE_PUBLISHED_COLUMNS_MODEL from '../../data/raw/tree_published_columns.preql?raw'
import CORE_MODEL from '../../data/raw/core.preql?raw'
import ECOREGION_COMMON_MODEL from '../../data/raw/ecoregion_common.preql?raw'
import ECOREGION_INFO_MODEL from '../../data/raw/ecoregion_info.preql?raw'
// Each city's own ecoregion, the dimension's second role; see
// data/raw/city_ecoregion.preql.
import CITY_ECOREGION_MODEL from '../../data/raw/city_ecoregion.preql?raw'
// The published cross-city landmark union, which the map chat imports as
// `landmark`; see data/raw/landmark_info.preql.
import LANDMARK_INFO_MODEL from '../../data/raw/landmark_info.preql?raw'
// The (city, species) tree-count summary the dashboard scope imports, so the
// planner can answer a per-species count without the city's tree file; see
// data/raw/tree_rollups.preql.
import TREE_ROLLUP_SOURCE_MODEL from '../../data/raw/tree_rollup_source.preql?raw'
// The tree-level buckets the finer summaries are keyed on (plant year, DBH
// class, dot-map cells); see data/raw/tree_summary_grains.preql.
import TREE_SUMMARY_GRAINS_MODEL from '../../data/raw/tree_summary_grains.preql?raw'

// The trees the map holds, as one datasource over the worker's `trees` view:
// what a published per-tree rule compiles against (models/map_trees.preql).
// A source here is only a file the resolver may import; it is in scope only
// for the rule's own import list (MAP_RULE_IMPORTS).
import MAP_TREES_MODEL from './models/map_trees.preql?raw'

export const ALL_MODEL_SOURCES = [
  { alias: 'tree_enrichment', contents: TREE_ENRICHMENT_MODEL },
  { alias: 'tree_info', contents: TREE_INFO_MODEL },
  { alias: 'tree_common', contents: TREE_COMMON_MODEL },
  { alias: 'tree_published_columns', contents: TREE_PUBLISHED_COLUMNS_MODEL },
  { alias: 'core', contents: CORE_MODEL },
  { alias: 'ecoregion_common', contents: ECOREGION_COMMON_MODEL },
  { alias: 'ecoregion_info', contents: ECOREGION_INFO_MODEL },
  { alias: 'city_ecoregion', contents: CITY_ECOREGION_MODEL },
  { alias: 'landmark_info', contents: LANDMARK_INFO_MODEL },
  { alias: 'tree_rollup_source', contents: TREE_ROLLUP_SOURCE_MODEL },
  { alias: 'tree_summary_grains', contents: TREE_SUMMARY_GRAINS_MODEL },
  { alias: 'map_trees', contents: MAP_TREES_MODEL },
]
