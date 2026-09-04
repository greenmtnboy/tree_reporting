/**
 * The values an ingest writes into `species` when a tree has no identifiable
 * species, and how to present them.
 *
 * Mirrors `SPECIES_SENTINELS` in `data/raw/_ingest_shared.py`. They are real
 * values rather than nulls so `species` — a Trilogy key — stays join-safe, but
 * they are not taxa: roughly 190k rows carry `Unknown`, which would otherwise
 * make it the single most common "species" in the dataset, ahead of Acer
 * platanoides. Every species rollup therefore has to exclude them explicitly;
 * a bare `species IS NOT NULL` does not, because a sentinel is never null.
 *
 * Most of the time the sentinel is plain `Unknown`, but a source that gives up
 * on the species while still recording "Palm" or "Shrub" has told us the growth
 * form, which is what the map icon and colour are chosen from — so those keep
 * their own sentinel instead of merging.
 *
 * Sentinels are excluded from the enrichment table by name and their
 * presentation is hardcoded here. That is deliberate: "Palm" is not a taxon,
 * and an LLM asked to describe one answers with a plausible, specific and wrong
 * species. `Unknown` was enriched once, in April 2026, and came back as
 * *Orania timikae* — a critically endangered New Guinea palm. It joined to
 * 189,139 trees across all fourteen cities, giving every unidentified tree a
 * palm icon, a palm photo and that description, until the row was purged and
 * this module took over.
 */

export interface SpeciesSentinel {
  /** The literal `species` value stored in the parquet. */
  species: string
  /** What to show where a common name would normally go. */
  label: string
  /** Drives the icon and colour, matching the enrichment `tree_form` values. */
  treeForm: string
  /** Shown in place of a species description. */
  note: string
}

export const UNKNOWN_SPECIES = 'Unknown'
export const PALM_SPECIES = 'Palm'
export const SHRUB_SPECIES = 'Shrub'
export const CACTUS_SPECIES = 'Cactus'
export const DEAD_SPECIES = 'Dead'

export const SPECIES_SENTINELS: readonly SpeciesSentinel[] = [
  {
    species: UNKNOWN_SPECIES,
    label: 'Species not recorded',
    treeForm: 'default',
    note: 'This tree is in the inventory, but its source did not record a species.',
  },
  {
    species: PALM_SPECIES,
    label: 'Palm (species not recorded)',
    treeForm: 'palm',
    note: 'The source recorded this as a palm without identifying the species.',
  },
  {
    species: SHRUB_SPECIES,
    label: 'Shrub (species not recorded)',
    treeForm: 'multi_trunk',
    note: 'The source recorded this as a shrub without identifying the species.',
  },
  {
    species: CACTUS_SPECIES,
    label: 'Cactus (species not recorded)',
    treeForm: 'columnar',
    note: 'The source recorded this as a cactus without identifying the species.',
  },
  {
    species: DEAD_SPECIES,
    label: 'Dead tree',
    treeForm: 'default',
    note: 'The source recorded this tree as dead, without identifying the species.',
  },
]

const BY_SPECIES = new Map(SPECIES_SENTINELS.map((s) => [s.species, s]))

/** The sentinel for a `species` value, or null when it is a real scientific name. */
export function speciesSentinel(value: string | null | undefined): SpeciesSentinel | null {
  if (!value) return null
  return BY_SPECIES.get(value) ?? null
}

/** True when `species` is a placeholder rather than a taxon. */
export function isSpeciesSentinel(value: string | null | undefined): boolean {
  return speciesSentinel(value) !== null
}

function sqlLiteral(value: string): string {
  return `'${value.replace(/'/g, "''")}'`
}

/** Guard for any query that counts, ranks or lists real species. */
export const REAL_SPECIES_PREDICATE =
  `species IS NOT NULL and species not in (` +
  SPECIES_SENTINELS.map((s) => sqlLiteral(s.species)).join(', ') +
  `)`

/**
 * `CASE` expression over a species column yielding the sentinel's value for
 * `field`, or NULL for a real species. Used by the worker so the override is
 * applied once, in `trees_fast`, rather than at every read site.
 */
function sentinelCaseSql(speciesExpr: string, field: 'label' | 'treeForm'): string {
  const branches = SPECIES_SENTINELS.map(
    (s) => `WHEN ${sqlLiteral(s.species)} THEN ${sqlLiteral(s[field])}`,
  ).join(' ')
  return `CASE ${speciesExpr} ${branches} ELSE NULL END`
}

export function sentinelLabelSql(speciesExpr: string): string {
  return sentinelCaseSql(speciesExpr, 'label')
}

export function sentinelTreeFormSql(speciesExpr: string): string {
  return sentinelCaseSql(speciesExpr, 'treeForm')
}
