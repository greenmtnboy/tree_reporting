import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { BARE_BRANCH_COLOR, foliagePhase, SHADE_PALETTE, snapShade, speciesShadeSql } from './foliageSeason'
import { colorSql, SPECIES_SHADE_TABLE, UNKNOWN_SHADE_COLOR } from '../workers/cityTables'

describe('foliage phase', () => {
  const BOSTON = 42.36, MELBOURNE = -37.81, TAIPEI = 25.03, SINGAPORE = 1.35
  const at = (month: number) => new Date(2026, month - 1, 15)

  test('follows the northern calendar north of the tropics', () => {
    expect(foliagePhase(at(7), BOSTON)).toBe('growing')
    expect(foliagePhase(at(10), BOSTON)).toBe('autumn')
    expect(foliagePhase(at(1), BOSTON)).toBe('bare')
    expect(foliagePhase(at(4), TAIPEI)).toBe('growing')
    expect(foliagePhase(at(11), TAIPEI)).toBe('autumn')
  })

  test('is six months out in the south and has no autumn in the tropics', () => {
    expect(foliagePhase(at(4), MELBOURNE)).toBe('autumn')
    expect(foliagePhase(at(7), MELBOURNE)).toBe('bare')
    expect(foliagePhase(at(12), MELBOURNE)).toBe('growing')
    expect(foliagePhase(at(10), SINGAPORE)).toBe('growing')
    expect(foliagePhase(at(1), SINGAPORE)).toBe('growing')
  })
})

describe('leaf-shade colour scheme', () => {
  let conn: DuckDBConnection
  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    await conn.run(`CREATE TABLE trees (tree_id VARCHAR, species VARCHAR);
      INSERT INTO trees SELECT 'maple-' || i, 'Acer rubrum' FROM range(40) t(i);
      INSERT INTO trees VALUES
        ('pine', 'Pinus strobus'),
        ('ginkgo', 'Ginkgo biloba'),
        ('mystery', 'Newtaxon alba'),
        ('bare-oak', 'Quercus nulla'),
        ('unknown-leaf', 'Unknown');
      CREATE TABLE species_enrichment (species VARCHAR, is_evergreen BOOLEAN, foliage_colors VARCHAR[], fall_foliage_colors VARCHAR[], trunk_color VARCHAR);
      INSERT INTO species_enrichment VALUES
        ('Acer rubrum', false, ['#3e7a3a'], ['#c62828', '#e65100'], '#7d7367'),
        ('Pinus strobus', true, ['#3b6b4a'], NULL, '#6b5a4b'),
        -- evergreen-ness never answered: autumn colour still applies, winter never bares it
        ('Ginkgo biloba', NULL, ['#5d9b3c'], ['#f4d03f'], NULL),
        -- deciduous with no shades at all: bare branches in winter, nothing otherwise
        ('Quercus nulla', false, NULL, NULL, NULL),
        ('Unknown', NULL, NULL, NULL, NULL);`)
  })
  afterAll(() => conn.closeSync())

  const colors = async (phase: 'growing' | 'autumn' | 'bare') => {
    await conn.run(`CREATE OR REPLACE TABLE ${SPECIES_SHADE_TABLE} AS ${speciesShadeSql(phase)}`)
    const color = colorSql('t', false, true)
    const rows = (await conn.runAndReadAll(`SELECT t.tree_id, ${color.expr} AS c FROM trees t ${color.join}`)).getRowObjects()
    expect(rows).toHaveLength(45)
    return Object.fromEntries(rows.map(row => [String(row.tree_id), row.c as string]))
  }

  test('growing season paints the leaf and greys a species without one', async () => {
    const c = await colors('growing')
    expect(c['maple-0']).toBe('#3e7a3a')
    expect(c.pine).toBe('#3b6b4a')
    expect(c.ginkgo).toBe('#5d9b3c')
    // No enrichment row, or a row with no shade: grey, not a special case.
    expect(c.mystery).toBe(UNKNOWN_SHADE_COLOR)
    expect(c['bare-oak']).toBe(UNKNOWN_SHADE_COLOR)
    expect(c['unknown-leaf']).toBe(UNKNOWN_SHADE_COLOR)
  })

  test('autumn spreads a two-shade species across its trees and keeps evergreens green', async () => {
    const c = await colors('autumn')
    const mapleShades = new Set(Object.entries(c).filter(([id]) => id.startsWith('maple-')).map(([, color]) => color))
    expect(mapleShades).toEqual(new Set(['#c62828', '#e65100']))
    expect(c.pine).toBe('#3b6b4a')
    expect(c.ginkgo).toBe('#f4d03f')
    expect(c['bare-oak']).toBe(UNKNOWN_SHADE_COLOR)
  })

  test('winter shows a deciduous tree by its bark and never bares an unknown', async () => {
    const c = await colors('bare')
    expect(c['maple-0']).toBe('#7d7367')
    expect(c.pine).toBe('#3b6b4a')
    expect(c.ginkgo).toBe('#5d9b3c')
    expect(c['bare-oak']).toBe(BARE_BRANCH_COLOR)
    expect(c['unknown-leaf']).toBe(UNKNOWN_SHADE_COLOR)
  })

  test('a tree keeps the same shade from one query to the next', async () => {
    const first = await colors('autumn')
    const second = await colors('autumn')
    expect(second).toEqual(first)
  })
})

describe('shade palette', () => {
  test('snaps every recorded shade to a handful of paint colours', () => {
    const palette = new Set<string>(SHADE_PALETTE)
    for (const hex of ['#4b732f', '#a8d572', '#2b330c', '#8e924c', '#C62828', '#e65100', '#f4d03f', '7d7367']) {
      expect(palette.has(snapShade(hex)!)).toBe(true)
    }
    expect(SHADE_PALETTE.length).toBeLessThanOrEqual(20)
  })

  test('keeps a shade in its family', () => {
    expect(snapShade('#c62828')).toBe('#c0392b')
    expect(snapShade('#f4d03f')).toBe('#e8c547')
    expect(snapShade('#e65100')).toBe('#d9661f')
    expect(snapShade(BARE_BRANCH_COLOR)).toBe(BARE_BRANCH_COLOR)
    expect(['#3f7034', '#5a8a3c']).toContain(snapShade('#4b732f'))
  })

  test('refuses what is not a colour', () => {
    expect(snapShade('red')).toBeNull()
    expect(snapShade("#abc'); DROP TABLE x; --")).toBeNull()
  })
})
