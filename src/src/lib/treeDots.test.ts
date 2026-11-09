import { describe, expect, it } from 'vitest'
import {
  buildDotLevels, buildPickGrid, circleOpacity, circleRadius, circleRadiusRange, heatOpacity, interpolateStops,
  levelColors, levelForKernel, visibleRanges, lngLatFrom3857, lngLatTo3857, MERCATOR_WORLD_M, pointsNear, relativeMatrix,
} from './treeDots'

function seeded(n: number, span: number) {
  let s = 12345
  const rnd = () => { s = (Math.imul(s, 1103515245) + 12345) >>> 0; return s / 2 ** 32 }
  const x = new Float32Array(n), y = new Float32Array(n)
  for (let i = 0; i < n; i++) { x[i] = rnd() * span; y[i] = rnd() * span }
  return { x, y }
}

describe('zoom curves', () => {
  it('interpolates and clamps like MapLibre', () => {
    const stops = [[10, 1], [12, 3]] as const
    expect(interpolateStops(stops, 9)).toBe(1)
    expect(interpolateStops(stops, 11)).toBe(2)
    expect(interpolateStops(stops, 13)).toBe(3)
  })
  it('crossfades the heat out by z15 and the circles in from z12.8 to z15.5', () => {
    expect(heatOpacity(5)).toBe(0)
    expect(heatOpacity(9)).toBeGreaterThan(0)
    expect(heatOpacity(15)).toBe(0)
    expect(circleOpacity(5.9)).toBe(0)
    expect(circleOpacity(9)).toBeGreaterThan(0.7)
    expect(circleOpacity(14)).toBeGreaterThan(0.75)
    expect(circleOpacity(15)).toBe(0)
  })
  it('sizes a circle by the square root of its trunk', () => {
    const range = circleRadiusRange(15)
    expect(range).toEqual([1.5, 4])
    expect(circleRadius(0, range)).toBe(1.5)
    expect(circleRadius(42, range)).toBeCloseTo(4)
    expect(circleRadius(200, range)).toBeCloseTo(4)
  })
})

describe('buildDotLevels', () => {
  it('keeps every tree in every level and puts each cell at its trees’ centroid', () => {
    const { x, y } = seeded(5000, 3000)
    const levels = buildDotLevels(x, y, 5000, [8, 64, 512])
    for (const level of levels) {
      expect(level.weight.reduce((a, b) => a + b, 0)).toBe(5000)
      for (let c = 0; c < level.count; c++) {
        // A centroid lies inside its own cell.
        expect(Math.floor(level.x[c] / level.cell)).toBeGreaterThanOrEqual(0)
      }
    }
    expect(levels[0].count).toBeGreaterThan(levels[1].count)
    expect(levels[1].count).toBeGreaterThan(levels[2].count)
    // Three trees in one 8 m cell merge into one weight-3 cell at their mean.
    const merged = buildDotLevels(new Float32Array([1, 2, 3, 100]), new Float32Array([1, 2, 3, 100]), 4, [8])[0]
    expect(merged.count).toBe(2)
    const c = merged.parent[0]
    expect(merged.weight[c]).toBe(3)
    expect(merged.x[c]).toBeCloseTo(2)
  })

  it('averages colours up the pyramid', () => {
    const x = new Float32Array([1, 2, 300])
    const y = new Float32Array([1, 2, 300])
    const levels = buildDotLevels(x, y, 3, [8, 1024])
    const colors = levelColors(levels, new Uint8Array([1, 2, 1]), ['#000000', '#ff0000', '#0000ff'], 3)
    const first = levels[0].parent[0] * 4
    expect([...colors[0].slice(first, first + 4)]).toEqual([128, 0, 128, 255])
    expect([...colors[1].slice(0, 3)]).toEqual([170, 0, 85])
  })

  it('lets a grey count as trees but barely as colour', () => {
    const levels = buildDotLevels(new Float32Array([1, 2]), new Float32Array([1, 2]), 2, [8])
    const [bytes] = levelColors(levels, new Uint8Array([1, 2]), ['#000000', '#ff0000', '#9a9a9a'], 2)
    // Red outweighs the grey six to one, and the cell's colour weight is (1 + 1/6) / 2 per tree.
    expect(bytes[0]).toBe(Math.round((255 + 154 / 6) / (7 / 6)))
    expect(bytes[3]).toBe(Math.round(255 * (7 / 6) / 2))
  })

  it('picks the coarsest level whose cells are under a third of the kernel', () => {
    const { x, y } = seeded(100, 100)
    const levels = buildDotLevels(x, y, 100, [8, 16, 32])
    expect(levelForKernel(levels, 100)).toBe(2)
    expect(levelForKernel(levels, 50)).toBe(1)
    expect(levelForKernel(levels, 5)).toBe(0)
  })
})

describe('pick grid', () => {
  it('visits every point within the radius', () => {
    const { x, y } = seeded(20000, 10000)
    const grid = buildPickGrid(x, y, 20000)
    const near: number[] = []
    pointsNear(grid, 5000, 5000, 150, (i) => { if (Math.hypot(x[i] - 5000, y[i] - 5000) <= 150) near.push(i) })
    const brute = [...x.keys()].filter((i) => Math.hypot(x[i] - 5000, y[i] - 5000) <= 150)
    expect(near.sort((a, b) => a - b)).toEqual(brute)
  })
})

describe('visibleRanges', () => {
  it('covers exactly the points of the overlapped cells, in few ranges', () => {
    const { x, y } = seeded(20000, 10000)
    const grid = buildPickGrid(x, y, 20000, 1 << 10, 512)
    const sx = Float32Array.from(grid.order, (i) => x[i])
    const sy = Float32Array.from(grid.order, (i) => y[i])
    const ranges = visibleRanges(grid, 2000, 3000, 4100, 3900)
    const inBox = new Set<number>()
    for (let k = 0; k < ranges.length; k += 2) for (let i = ranges[k]; i < ranges[k + 1]; i++) inBox.add(i)
    for (let i = 0; i < 20000; i++) {
      if (sx[i] >= 2000 && sx[i] <= 4100 && sy[i] >= 3000 && sy[i] <= 3900) expect(inBox.has(i)).toBe(true)
    }
    expect(inBox.size).toBeLessThan(20000 / 4)
    // A box over everything is one range.
    expect(visibleRanges(grid, -1e9, -1e9, 1e9, 1e9)).toEqual([0, 20000])
  })
})

describe('camera', () => {
  it('maps local metres to the same clip position as the Mercator matrix', () => {
    // A plain scale-translate matrix standing in for MapLibre's.
    const m = [3, 0, 0, 0, 0, 5, 0, 0, 0, 0, 1, 0, -1, 2, 0, 1]
    const [cx, cy] = lngLatTo3857(-71.06, 42.36)
    const rel = relativeMatrix(m, cx, cy)
    const dx = 120, dy = -40
    const mercX = (cx + dx + MERCATOR_WORLD_M / 2) / MERCATOR_WORLD_M
    const mercY = (MERCATOR_WORLD_M / 2 - (cy + dy)) / MERCATOR_WORLD_M
    expect(rel[0] * dx + rel[4] * dy + rel[12]).toBeCloseTo(m[0] * mercX + m[12], 9)
    expect(rel[1] * dx + rel[5] * dy + rel[13]).toBeCloseTo(m[5] * mercY + m[13], 9)
  })
  it('round-trips EPSG:3857', () => {
    const [x, y] = lngLatTo3857(-71.06, 42.36)
    const [lng, lat] = lngLatFrom3857(x, y)
    expect(lng).toBeCloseTo(-71.06, 9)
    expect(lat).toBeCloseTo(42.36, 9)
  })
})
