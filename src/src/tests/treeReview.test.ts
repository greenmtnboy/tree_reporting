import { describe, expect, it } from 'vitest'
import { cityReviewParquetUrl, reviewNote, type TreeReview } from '../lib/treeReview'

const FLAGGED: TreeReview = { flag: 'dbh_outlier', speciesMeanDbh: 13.2 }

describe('reviewNote', () => {
  it('asks for a second look while nobody has checked in', () => {
    expect(reviewNote(FLAGGED, 0, 50)).toBe(
      "This tree's 50″ trunk diameter is unusually large for its species (about 13″ on average here). Check in or suggest a correction to give it a second look.",
    )
  })

  it('goes once someone has checked in, and stays off while the count is unknown', () => {
    expect(reviewNote(FLAGGED, 1, 50)).toBeNull()
    expect(reviewNote(FLAGGED, null, 50)).toBeNull()
    expect(reviewNote(FLAGGED, undefined, 50)).toBeNull()
  })

  it('needs a flag, and words the note without the numbers it lacks', () => {
    expect(reviewNote(null, 0, 50)).toBeNull()
    expect(reviewNote({ flag: 'dbh_outlier', speciesMeanDbh: null }, 0, null)).toBe(
      "This tree's recorded trunk diameter is unusually large for its species. Check in or suggest a correction to give it a second look.",
    )
  })
})

describe('cityReviewParquetUrl', () => {
  it('names the city review file and refuses anything but a city code', () => {
    expect(cityReviewParquetUrl('USSFO')).toMatch(/\/ussfo_tree_review_v\d+\.parquet$/)
    expect(cityReviewParquetUrl("x'); DROP")).toBeNull()
  })
})
