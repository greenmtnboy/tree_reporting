import { describe, expect, test } from 'vitest'
import { queryBounds, queryShape, ruleBlocker, unboundedQueryError } from './chatQueryScope'

describe('what ground a chat query is about', () => {
  test('a condition on one tree and its species is a per-tree rule', () => {
    for (const query of [
      "select tree_id where genus = 'Quercus';",
      "SELECT tree_id, case when diameter_at_breast_height >= 20 then '#FF69B4' else '#4169E1' end as override_color WHERE is_evergreen = true",
      "where city = 'USBOS' and species like 'Gleditsia%' select tree_id, :override_color as override_color;",
      'select tree_id where 11 in bloom_months and latitude between 37.7 and 37.8',
      // Names that only contain an aggregate's name, and functions that are not one.
      'select tree_id where mature_height_max_ft > 50 and lifespan_min_years > 100 and coalesce(diameter_at_breast_height, 0) > 3',
      "select tree_id where date_diff(plant_date, current_date(), year) > 50",
    ]) expect(ruleBlocker(query), query).toBeNull()
  })

  test('ranking, limiting and aggregating are not: the answer depends on the rows asked over', () => {
    expect(ruleBlocker('select tree_id, --diameter_at_breast_height order by diameter_at_breast_height desc limit 50;')).toMatch(/LIMIT/)
    expect(ruleBlocker('select tree_id order by plant_date asc;')).toMatch(/ORDER BY/)
    expect(ruleBlocker('select tree_id where diameter_at_breast_height > avg(diameter_at_breast_height) by species;')).toMatch(/aggregates \(avg\)/)
    expect(ruleBlocker('select species, count(tree_id) as n;')).toMatch(/aggregates \(count\)/)
    expect(ruleBlocker('select tree_id where rank(tree_id) over (species) <= 3;')).toMatch(/rank/)
    expect(ruleBlocker('select tree_id having diameter_at_breast_height > 3;')).toMatch(/HAVING/)
  })

  test('words inside a string or a comment decide nothing', () => {
    expect(queryShape("where species = 'Limit order by count(' # limit 5\nselect tree_id")).not.toMatch(/limit|count/)
    expect(ruleBlocker("select tree_id where common_names like '%limit%' # order by nothing")).toBeNull()
    expect(queryBounds("select tree_id where description = 'city = here'")).toBeNull()
  })

  test('a city filter or a latitude and longitude range bounds a query', () => {
    expect(queryBounds("select count(tree_id) as n where city = 'USSFO';")).toBe('city')
    expect(queryBounds("select species, count(tree_id) as n where city in ('USSFO', 'USBOS');")).toBe('city')
    expect(queryBounds('select count(tree_id) as n where latitude between 37.7 and 37.8 and longitude between -122.5 and -122.4;')).toBe('box')
    expect(queryBounds('select count(tree_id) as n where latitude >= 37.7 and latitude < 37.8 and longitude > -122.5 and -122.4 > longitude;')).toBe('box')
  })

  test('one coordinate, a city in the output, or nothing does not', () => {
    expect(queryBounds('select count(tree_id) as n;')).toBeNull()
    expect(queryBounds('select city, count(tree_id) as n;')).toBeNull()
    expect(queryBounds("select count(tree_id) as n where city != 'USSFO';")).toBeNull()
    expect(queryBounds('select count(tree_id) as n where latitude > 37.7;')).toBeNull()
    expect(queryBounds('select latitude, longitude, tree_id limit 5;')).toBeNull()
  })

  test('the refusal says how to bound the query', () => {
    const error = unboundedQueryError('run_query')
    expect(error).toMatch(/^run_query was not run/)
    expect(error).toContain("city = 'USSFO'")
    expect(error).toContain('lookup_landmark')
    expect(error).toContain('latitude BETWEEN')
  })
})
