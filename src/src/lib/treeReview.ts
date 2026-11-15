import { useDuckDB } from '../composables/useDuckDB'
import { e2eFixtures } from './e2eFixtures'
import { REMOTE_TREES_BASE_URL, TREE_DATA_VERSION } from '../workers/parquetUrls'

/**
 * Why a tree's recorded values want a second look (data/raw/city_derived.preql,
 * `city_tree_review`). `dbh_outlier`: the diameter is 30"+ and over three
 * times its species' mean in the city.
 */
export type ReviewFlag = 'dbh_outlier'

export interface TreeReview {
  flag: ReviewFlag
  /** The species' mean diameter in the city, in inches, for the card's note. */
  speciesMeanDbh: number | null
}

const quote = (s: string) => `'${s.replace(/'/g, "''")}'`

/** The city's review file: only its flagged trees, a few thousand rows at most. */
export function cityReviewParquetUrl(city: string): string | null {
  const lower = city.toLowerCase()
  if (!/^[a-z]{2}[a-z]{3}$/.test(lower)) return null
  return `${REMOTE_TREES_BASE_URL}/${lower}_tree_review_v${TREE_DATA_VERSION}.parquet`
}

/**
 * One tree's review flag, or null. Best-effort: a city whose derived job has
 * not yet published its review file, or any failed read, is "no flag".
 */
export async function loadTreeReview(treeId: string, city: string): Promise<TreeReview | null> {
  const fixture = e2eFixtures()
  if (fixture) return fixture.treeReviews?.[treeId] ?? null
  const url = cityReviewParquetUrl(city)
  if (!url) return null
  try {
    const { rows } = await useDuckDB().query(
      `SELECT review_flag, species_mean_dbh FROM read_parquet(${quote(url)}) WHERE tree_id = ${quote(treeId)} LIMIT 1`,
    )
    const row = rows[0]
    if (!row || row.review_flag !== 'dbh_outlier') return null
    const mean = row.species_mean_dbh == null ? null : Number(row.species_mean_dbh)
    return { flag: 'dbh_outlier', speciesMeanDbh: Number.isFinite(mean) ? mean : null }
  } catch (err) {
    console.warn('[TreeCard] review flag unavailable', err)
    return null
  }
}

/**
 * The card's note: shown while the tree is flagged and nobody has checked in
 * since (a visit is the second look). An unknown check-in count is not
 * evidence that nobody has visited, as for an unconfirmed prediction.
 */
export function reviewNote(
  review: TreeReview | null,
  checkinCount: number | null | undefined,
  dbhInches: number | null | undefined,
): string | null {
  if (!review || checkinCount !== 0) return null
  const dbh = dbhInches != null ? `${Math.round(dbhInches)}″` : 'recorded'
  const mean = review.speciesMeanDbh != null ? ` (about ${Math.round(review.speciesMeanDbh)}″ on average here)` : ''
  return `This tree's ${dbh} trunk diameter is unusually large for its species${mean}. Check in or suggest a correction to give it a second look.`
}
