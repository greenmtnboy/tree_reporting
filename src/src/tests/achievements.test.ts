import { describe, expect, it } from 'vitest'
import {
  ACHIEVEMENTS,
  buildStats,
  evaluateAchievements,
  plantYearFrom,
  type AchievementCheckin,
  type AchievementSubmission,
} from '../lib/achievements'

function submission(overrides: Partial<AchievementSubmission> = {}): AchievementSubmission {
  return {
    city: 'USSFO',
    species: null,
    submittedAt: new Date('2026-08-01T12:00:00'),
    photoCount: 1,
    ...overrides,
  }
}

function checkin(overrides: Partial<AchievementCheckin> = {}): AchievementCheckin {
  return {
    city: 'USSFO',
    at: new Date('2026-08-01T12:00:00'),
    hasPhoto: false,
    species: null,
    treeForm: null,
    dbhInches: null,
    plantYear: null,
    speciesCityCount: null,
    ...overrides,
  }
}

function earnedIds(subs: AchievementSubmission[], chks: AchievementCheckin[]): Set<string> {
  return new Set(
    evaluateAchievements(subs, chks)
      .filter((a) => a.earned)
      .map((a) => a.id),
  )
}

describe('achievement evaluation', () => {
  it('earns nothing with no contributions', () => {
    expect(earnedIds([], []).size).toBe(0)
  })

  it('unique achievement ids and metrics that exist on stats', () => {
    const ids = new Set(ACHIEVEMENTS.map((a) => a.id))
    expect(ids.size).toBe(ACHIEVEMENTS.length)
    const stats = buildStats([], [])
    for (const a of ACHIEVEMENTS) {
      expect(stats[a.metric]).toBeDefined()
    }
  })

  it('first submission and first check-in', () => {
    const earned = earnedIds([submission()], [checkin()])
    expect(earned.has('first-submission')).toBe(true)
    expect(earned.has('first-checkin')).toBe(true)
    expect(earned.has('submit-5')).toBe(false)
  })

  it('count milestones use their own track', () => {
    const subs = Array.from({ length: 10 }, () => submission())
    const earned = earnedIds(subs, [])
    expect(earned.has('submit-5')).toBe(true)
    expect(earned.has('submit-10')).toBe(true)
    expect(earned.has('checkin-5')).toBe(false)
  })

  it('species diversity merges submissions and check-ins, ignoring unknown/case', () => {
    const subs = [
      submission({ species: 'Platanus x hispanica' }),
      submission({ species: 'platanus X hispanica' }),
      submission({ species: 'Unknown' }),
      submission({ species: '  ' }),
      submission({ species: 'Quercus agrifolia' }),
    ]
    const chks = [
      checkin({ species: 'Ginkgo biloba' }),
      checkin({ species: 'Prunus' }),
      checkin({ species: 'Acer rubrum' }),
    ]
    const stats = buildStats(subs, chks)
    expect(stats.distinctSpeciesCount).toBe(5)
    expect(earnedIds(subs, chks).has('species-5')).toBe(true)
  })

  it('identified contributions count species-bearing rows only', () => {
    const stats = buildStats(
      [submission({ species: 'Prunus' }), submission()],
      [checkin({ species: 'Unknown' })],
    )
    expect(stats.identifiedContributionCount).toBe(1)
  })

  it('tree form achievements need the snapshot', () => {
    const legacy = checkin() // pre-feature check-in without a snapshot
    expect(earnedIds([], [legacy]).has('palm')).toBe(false)

    const chks = [
      checkin({ treeForm: 'palm' }),
      checkin({ treeForm: 'conifer' }),
      checkin({ treeForm: 'broadleaf' }),
      checkin({ treeForm: 'weeping' }),
    ]
    const earned = earnedIds([], chks)
    expect(earned.has('palm')).toBe(true)
    expect(earned.has('forms-4')).toBe(true)
    expect(earned.has('conifer-5')).toBe(false)
  })

  it('rarity thresholds', () => {
    expect(earnedIds([], [checkin({ speciesCityCount: 10 })]).has('rare-find')).toBe(true)
    expect(earnedIds([], [checkin({ speciesCityCount: 11 })]).has('rare-find')).toBe(false)
    expect(earnedIds([], [checkin({ speciesCityCount: 1 })]).has('one-of-one')).toBe(true)
    expect(earnedIds([], [checkin({ speciesCityCount: 2 })]).has('one-of-one')).toBe(false)
    // 0 means the count query returned nothing sensible — never rare.
    expect(earnedIds([], [checkin({ speciesCityCount: 0 })]).has('rare-find')).toBe(false)
  })

  it('size and age', () => {
    expect(earnedIds([], [checkin({ dbhInches: 40 })]).has('giant')).toBe(true)
    expect(earnedIds([], [checkin({ dbhInches: 39.5 })]).has('giant')).toBe(false)
    const oldYear = new Date().getFullYear() - 50
    expect(earnedIds([], [checkin({ plantYear: oldYear })]).has('old-growth')).toBe(true)
    expect(earnedIds([], [checkin({ plantYear: oldYear + 1 })]).has('old-growth')).toBe(false)
  })

  it('distinct cities across both contribution kinds', () => {
    const earned = earnedIds(
      [submission({ city: 'USSFO' })],
      [checkin({ city: 'usbos' })],
    )
    expect(earned.has('cities-2')).toBe(true)
  })

  it('time-of-day uses local hours', () => {
    const dawn = checkin({ at: new Date('2026-08-01T06:59:00') })
    const night = checkin({ at: new Date('2026-08-01T21:00:00') })
    const midday = checkin({ at: new Date('2026-08-01T12:00:00') })
    const earned = earnedIds([], [dawn, night, midday])
    expect(earned.has('dawn')).toBe(true)
    expect(earned.has('night')).toBe(true)
    const stats = buildStats([], [midday])
    expect(stats.dawnCheckinCount).toBe(0)
    expect(stats.nightCheckinCount).toBe(0)
  })

  it('distinct days across both contribution kinds', () => {
    const subs = [1, 2, 3].map((d) => submission({ submittedAt: new Date(2026, 7, d) }))
    const chks = [4, 5, 6, 7].map((d) => checkin({ at: new Date(2026, 7, d) }))
    // Same-day duplicates don't inflate the count.
    chks.push(checkin({ at: new Date(2026, 7, 4, 18) }))
    const stats = buildStats(subs, chks)
    expect(stats.distinctDayCount).toBe(7)
    expect(earnedIds(subs, chks).has('days-7')).toBe(true)
  })

  it('progress is clamped to the target', () => {
    const subs = Array.from({ length: 7 }, () => submission())
    const submit5 = evaluateAchievements(subs, []).find((a) => a.id === 'submit-5')
    expect(submit5?.progress).toBe(5)
    expect(submit5?.earned).toBe(true)
  })

  it('multi-photo submission needs 3+ photos on one submission', () => {
    expect(earnedIds([submission({ photoCount: 3 })], []).has('multi-photo')).toBe(true)
    expect(
      earnedIds([submission({ photoCount: 2 }), submission({ photoCount: 2 })], []).has(
        'multi-photo',
      ),
    ).toBe(false)
  })
})

describe('plantYearFrom', () => {
  it('parses ISO strings, epoch millis, Dates, and rejects junk', () => {
    expect(plantYearFrom('1972-04-01')).toBe(1972)
    expect(plantYearFrom(new Date(Date.UTC(1990, 5, 15)))).toBe(1990)
    expect(plantYearFrom(Date.UTC(2001, 0, 2))).toBe(2001)
    expect(plantYearFrom(null)).toBeNull()
    expect(plantYearFrom(undefined)).toBeNull()
    expect(plantYearFrom('not a date')).toBeNull()
  })
})
