import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import { DuckDBInstance, type DuckDBConnection } from '@duckdb/node-api'
import { applyMapUrlFilters, DBH_COLOR_SQL, readMapColorBy, readMapUrlFilters } from './mapUrlFilters'

describe('public map URL filters', () => {
  let conn: DuckDBConnection
  beforeAll(async () => {
    conn = await (await DuckDBInstance.create(':memory:')).connect()
    await conn.run(`CREATE TABLE trees (tree_id VARCHAR, species VARCHAR, city VARCHAR, diameter_at_breast_height DOUBLE);
      INSERT INTO trees VALUES
      ('plane', 'Platanus x hispanica', 'USSFO', 24),
      ('oak', 'Quercus robur', 'USSFO', 12),
      ('genus-only', 'Quercus', 'USSFO', NULL),
      ('cultivar', 'Quercus robur ''Fastigiata''', 'USSFO', 6),
      ('small', 'Quercus alba', 'USSFO', 3),
      ('invalid', 'Quercus alba', 'USSFO', 0),
      ('other-city', 'Platanus x hispanica', 'USNYC', 30);`)
  })
  afterAll(() => conn.closeSync())

  const ids = async (query: string) => (await conn.runAndReadAll(query)).getRowObjects().map(row => row.tree_id).sort()
  const base = "SELECT tree_id FROM trees WHERE city = 'USSFO';"

  test('matches exact species and intersects city scope even without a species projection', async () => {
    expect(await ids(applyMapUrlFilters(base, readMapUrlFilters({ 'filter.species': 'Platanus x hispanica' })))).toEqual(['plane'])
    expect(await ids(applyMapUrlFilters(base, readMapUrlFilters({ 'filter.species': 'Quercus' })))).toEqual(['genus-only'])
    expect(await ids(applyMapUrlFilters(base, readMapUrlFilters({ 'filter.species': 'No such species' })))).toEqual([])
  })

  test('repeated values are OR, different fields are AND, unsupported fields are ignored', async () => {
    const filters = readMapUrlFilters({ 'filter.species': ['Quercus robur', 'Platanus x hispanica', 'Quercus robur'], 'filter.genus': 'Quercus', 'filter.sql': '1=1' })
    expect(filters.species).toHaveLength(2)
    expect(await ids(applyMapUrlFilters(base, filters))).toEqual(['oak'])
  })

  test('quotes and SQL-looking names remain literal values', async () => {
    expect(await ids(applyMapUrlFilters(base, readMapUrlFilters({ 'filter.species': "Quercus robur 'Fastigiata'" })))).toEqual(['cultivar'])
    expect(await ids(applyMapUrlFilters(base, readMapUrlFilters({ 'filter.species': "x') OR true --" })))).toEqual([])
  })

  test('empty filters leave the original query and color defaults intact', () => {
    const empty = readMapUrlFilters({ 'filter.species': [null, '', '  '] })
    expect(applyMapUrlFilters(base, empty)).toBe(base)
    expect(readMapColorBy({}, empty)).toBe('tree_form')
    const species = readMapUrlFilters({ 'filter.species': 'Quercus' })
    expect(readMapColorBy({}, species)).toBe('dbh')
    expect(readMapColorBy({ colorBy: 'tree_form' }, species)).toBe('tree_form')
    expect(readMapColorBy({ colorBy: 'invalid' }, species)).toBe('dbh')
  })

  test('DBH colors use real measurements, with explicit unknowns and consistent boundaries', async () => {
    const colors = Object.fromEntries((await conn.runAndReadAll(DBH_COLOR_SQL)).getRowObjects().map(row => [row.tree_id, row.override_color]))
    expect(colors).toMatchObject({ small: '#c7e9b4', cultivar: '#7fcdbb', oak: '#41b6c4', plane: '#225ea8', 'genus-only': '#9a9a9a', invalid: '#9a9a9a' })
  })
})
