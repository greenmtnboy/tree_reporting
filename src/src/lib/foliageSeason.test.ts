import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import {
  BARE_BRANCH_COLOR, FINE_SHADE_BUDGET, fineShadeMap, FoliageLookup, foliageCell, foliageDefaultsSql, foliagePhase, foliageSpeciesSql,
  leafOffBit, leafOffMonths, SHADE_PALETTE, snapShade, speciesCodeSql, speciesShadeSql,
} from './foliageSeason'
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

describe('leaf-off months', () => {
  const months = (latitude: number) => [...Array(12).keys()].filter(m => leafOffMonths(latitude) & (1 << m)).map(m => m + 1)

  test('lengthen poleward and are six months out in the south', () => {
    expect(months(1.35)).toEqual([])
    expect(months(23.4)).toEqual([])
    expect(months(-23.4)).toEqual([])
    expect(months(26.15)).toEqual([1])
    expect(months(29.76)).toEqual([1, 2, 12])
    expect(months(42.36)).toEqual([1, 2, 3, 12])
    expect(months(60.2)).toEqual([1, 2, 3, 4, 11, 12])
    expect(months(-37.81)).toEqual([6, 7, 8, 9])
  })

  test('agree with the phase table and the month bit', () => {
    for (const latitude of [-60.5, -37.8, -23.6, 0, 25.03, 29.76, 42.36, 60.2]) {
      for (let month = 0; month < 12; month++) {
        const date = new Date(2027, month, 15)
        expect((leafOffMonths(latitude) & leafOffBit(date)) !== 0).toBe(foliagePhase(date, latitude) === 'bare')
      }
    }
  })
})

describe('leaf-off lookup', () => {
  let conn: DuckDBConnection
  let dir: string
  const BOSTON = [42.36, -71.06], NAPLES = [26.15, -81.79]
  beforeAll(async () => {
    dir = mkdtempSync(join(tmpdir(), 'foliage-')).split('\\').join('/')
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    // Trees carry their species as the world's ENUM does (workers/cityTables.ts), which holds
    // species the enrichment never saw.
    await conn.run(`CREATE TABLE species_enrichment (species VARCHAR, genus VARCHAR, is_evergreen BOOLEAN);
      INSERT INTO species_enrichment VALUES
        ('Quercus', 'Quercus', NULL),
        ('Quercus virginiana', 'Quercus', true),
        ('Quercus rubra', 'Quercus', false),
        ('Acer', 'Acer', false),
        ('Ginkgo biloba', 'Ginkgo', NULL);
      CREATE TABLE published (cell BIGINT, genus VARCHAR, deciduous_share DOUBLE);
      INSERT INTO published VALUES
        (47628, 'Quercus', 1.0),
        ((26 + 90) * 360 + -82 + 180, 'Quercus', 0.0),
        (47628, 'Ginkgo', NULL);
      COPY published TO '${dir}/foliage_defaults.parquet' (FORMAT PARQUET);
      CREATE TYPE species_t AS ENUM ('Acer', 'Ginkgo biloba', 'Newtaxon alba', 'Quercus', 'Quercus rubra', 'Quercus virginiana');
      CREATE TABLE trees (tree_id VARCHAR, species species_t, latitude DOUBLE, longitude DOUBLE);
      INSERT INTO trees VALUES
        ('boston-oak', 'Quercus', ${BOSTON}),
        ('naples-oak', 'Quercus', ${NAPLES}),
        ('boston-live-oak', 'Quercus virginiana', ${BOSTON}),
        ('naples-red-oak', 'Quercus rubra', ${NAPLES}),
        ('boston-maple', 'Acer', ${BOSTON}),
        ('boston-ginkgo', 'Ginkgo biloba', ${BOSTON}),
        ('mystery', 'Newtaxon alba', ${BOSTON}),
        ('no-species', NULL, ${BOSTON});`)
  })
  afterAll(() => {
    conn.closeSync()
    rmSync(dir, { recursive: true, force: true })
  })

  /** The lookup as the worker builds it, from the same SQL. */
  const lookup = async () => {
    const species = (await conn.runAndReadAll(foliageSpeciesSql('species_t'))).getRowObjects()
    const defaults = (await conn.runAndReadAll(foliageDefaultsSql(`${dir}/foliage_defaults.parquet`))).getRowObjects()
    return new FoliageLookup(
      species.map(r => ({ code: Number(r.code), deciduous: r.deciduous as boolean | null, open_genus: r.open_genus as string | null })),
      defaults.map(r => ({ cell: Number(r.cell), genus: String(r.genus), deciduous: !!r.deciduous })),
    )
  }

  test('keys a tree on the cell the published table is', () => {
    // data/raw/tests/test_foliage_defaults.py pins the same cell from the model's side.
    expect(foliageCell(BOSTON[0], BOSTON[1])).toBe(47628)
  })

  test("a generic oak is bare in Boston's winter and never in Naples", async () => {
    const foliage = await lookup()
    const rows = (await conn.runAndReadAll(`SELECT tree_id, ${speciesCodeSql('t')} AS code, latitude, longitude FROM trees t`)).getRowObjects()
    const mask = Object.fromEntries(rows.map(r => [String(r.tree_id), foliage.leafOff(Number(r.code), Number(r.latitude), Number(r.longitude))]))
    expect(mask).toEqual({
      'boston-oak': leafOffMonths(BOSTON[0]),
      'naples-oak': 0,
      // A binomial keeps its own habit wherever it stands.
      'boston-live-oak': 0,
      'naples-red-oak': leafOffMonths(NAPLES[0]),
      // No published share for the genus here: the enrichment's answer for the genus row stands.
      'boston-maple': leafOffMonths(BOSTON[0]),
      // A null share (too few identified trees) and an unknown habit: in leaf.
      'boston-ginkgo': 0,
      mystery: 0,
      'no-species': 0,
    })
    expect(leafOffMonths(BOSTON[0])).not.toBe(0)
    expect(leafOffMonths(NAPLES[0])).not.toBe(0)
  })

  test('without the published shares a generic tree keeps the enrichment answer', async () => {
    const species = (await conn.runAndReadAll(foliageSpeciesSql('species_t'))).getRowObjects()
    const foliage = new FoliageLookup(species.map(r => ({ code: Number(r.code), deciduous: r.deciduous as boolean | null, open_genus: r.open_genus as string | null })), [])
    // An ENUM's codes are its values' places in the order the type lists them.
    const code = (name: string) => ['Acer', 'Ginkgo biloba', 'Newtaxon alba', 'Quercus', 'Quercus rubra', 'Quercus virginiana'].indexOf(name)
    expect(foliage.leafOff(code('Quercus'), BOSTON[0], BOSTON[1])).toBe(0)
    expect(foliage.leafOff(code('Acer'), BOSTON[0], BOSTON[1])).toBe(leafOffMonths(BOSTON[0]))
    expect(FoliageLookup.none().leafOff(0, BOSTON[0], BOSTON[1])).toBe(0)
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

  test('keeps a muted green leaf among the greens', () => {
    const greens = new Set<string>(SHADE_PALETTE.slice(0, 8))
    // Pinus parviflora, Pinus sylvestris, Abies sibirica, Araucaria heterophylla as the enrichment table records them.
    for (const hex of ['#778e68', '#303016', '#70714c', '#50502d']) {
      expect(greens.has(snapShade(hex)!), hex).toBe(true)
    }
    expect(snapShade('#778e68')).toBe('#5f8a78')
  })

  test('refuses what is not a colour', () => {
    expect(snapShade('red')).toBeNull()
    expect(snapShade("#abc'); DROP TABLE x; --")).toBeNull()
  })
})

describe('fine shade map', () => {
  const channel = (hex: string, i: number) => parseInt(hex.slice(1 + 2 * i, 3 + 2 * i), 16)
  // Every shade a 4-step grid can make: far more than the budget.
  const many: string[] = []
  for (let r = 0; r < 256; r += 8) for (let g = 0; g < 256; g += 4) for (let b = 0; b < 256; b += 32) {
    many.push('#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join(''))
  }

  test('paints no more distinct colours than the budget', () => {
    const map = fineShadeMap(many)
    expect(map.size).toBe(many.length)
    expect(new Set(map.values()).size).toBeLessThanOrEqual(FINE_SHADE_BUDGET)
    expect(new Set(fineShadeMap(many, 12).values()).size).toBeLessThanOrEqual(12)
  })

  test('keeps a shade exact while the budget allows', () => {
    const map = fineShadeMap(['#778e68', '#303016', '#c0392b'])
    expect([...map.entries()]).toEqual([['#778e68', '#778e68'], ['#303016', '#303016'], ['#c0392b', '#c0392b']])
  })

  test('paints a shade near itself', () => {
    let worst = 0
    for (const [hex, paint] of fineShadeMap(many)) {
      for (let i = 0; i < 3; i++) worst = Math.max(worst, Math.abs(channel(hex, i) - channel(paint, i)))
    }
    expect(worst).toBeLessThan(40)
  })

  test('leaves out what is not a colour', () => {
    expect(fineShadeMap(['red', "#abc'); DROP TABLE x; --", '#778e68']).size).toBe(1)
  })
})
