/**
 * The species value an ingest writes when a tree has no identifiable species.
 *
 * Mirrors `UNKNOWN_SPECIES` in `data/raw/_ingest_shared.py`. It is a real value
 * rather than a null so `species` — a Trilogy key — stays join-safe, but it is
 * not a taxon: roughly 190k rows carry it, which would otherwise make it the
 * single most common "species" in the dataset, ahead of Acer platanoides.
 *
 * Every species rollup therefore has to exclude it explicitly. A bare
 * `species IS NOT NULL` no longer does, because the sentinel is never null.
 */
export const UNKNOWN_SPECIES = 'Unknown'

/** Guard for any query that counts, ranks or lists real species. */
export const REAL_SPECIES_PREDICATE = `species IS NOT NULL and species != '${UNKNOWN_SPECIES}'`
