import { describe, test, expect } from 'vitest'
import { DuckDBInstance } from '@duckdb/node-api'
import {
  SPECIES_SENTINELS,
  REAL_SPECIES_PREDICATE,
  UNKNOWN_SPECIES,
  PALM_SPECIES,
  speciesSentinel,
  isSpeciesSentinel,
  sentinelLabelSql,
  sentinelTreeFormSql,
} from './species'

/**
 * The SQL helpers are interpolated into the worker's `trees_fast` build, where
 * a mistake shows up as every unidentified tree in a city getting the wrong
 * icon rather than as an error. Run them through DuckDB rather than asserting
 * on the generated string.
 */
async function query(sql: string): Promise<Record<string, unknown>[]> {
  const instance = await DuckDBInstance.create(':memory:')
  const conn = await instance.connect()
  try {
    return (await conn.runAndReadAll(sql)).getRowObjects()
  } finally {
    conn.closeSync()
  }
}

const SAMPLE = `(VALUES ('Acer rubrum'), ('Unknown'), ('Palm'), ('Shrub'), ('Cactus'), ('Dead'), (NULL)) AS t(species)`

describe('species sentinels', () => {
  test('speciesSentinel resolves each sentinel and nothing else', () => {
    for (const sentinel of SPECIES_SENTINELS) {
      expect(speciesSentinel(sentinel.species)).toEqual(sentinel)
    }
    expect(speciesSentinel('Acer rubrum')).toBeNull()
    expect(speciesSentinel(null)).toBeNull()
    expect(speciesSentinel('')).toBeNull()
    expect(isSpeciesSentinel(UNKNOWN_SPECIES)).toBe(true)
    expect(isSpeciesSentinel('Quercus robur')).toBe(false)
  })

  test('every sentinel has a non-empty label, form and note', () => {
    for (const s of SPECIES_SENTINELS) {
      expect(s.label.length, `${s.species} label`).toBeGreaterThan(0)
      expect(s.treeForm.length, `${s.species} treeForm`).toBeGreaterThan(0)
      expect(s.note.length, `${s.species} note`).toBeGreaterThan(0)
    }
  })

  test('sentinel tree forms are values the worker CASE maps to an icon', () => {
    // The worker collapses anything it does not recognise to 'default', so an
    // unlisted form silently loses the icon it was meant to keep.
    const known = new Set([
      'palm', 'broadleaf', 'conifer', 'spreading', 'columnar',
      'ornamental', 'weeping', 'multi_trunk', 'default',
    ])
    for (const s of SPECIES_SENTINELS) {
      expect(known, `${s.species} -> ${s.treeForm}`).toContain(s.treeForm)
    }
  })

  test('sentinelLabelSql names each sentinel and leaves a real species null', async () => {
    const rows = await query(
      `SELECT species, ${sentinelLabelSql('species')} AS label FROM ${SAMPLE}`,
    )
    const byName = new Map(rows.map((r) => [r.species as string | null, r.label]))
    expect(byName.get('Acer rubrum')).toBeNull()
    expect(byName.get(null)).toBeNull()
    for (const s of SPECIES_SENTINELS) {
      expect(byName.get(s.species), `label for ${s.species}`).toBe(s.label)
    }
  })

  test('sentinelTreeFormSql gives the form sentinels their own icon', async () => {
    const rows = await query(
      `SELECT species, ${sentinelTreeFormSql('species')} AS form FROM ${SAMPLE}`,
    )
    const byName = new Map(rows.map((r) => [r.species as string | null, r.form]))
    expect(byName.get('Acer rubrum')).toBeNull()
    expect(byName.get(PALM_SPECIES)).toBe('palm')
    expect(byName.get(UNKNOWN_SPECIES)).toBe('default')
  })

  test('REAL_SPECIES_PREDICATE excludes every sentinel and keeps real species', async () => {
    const rows = await query(
      `SELECT species FROM ${SAMPLE} WHERE ${REAL_SPECIES_PREDICATE}`,
    )
    expect(rows.map((r) => r.species)).toEqual(['Acer rubrum'])
  })
})
