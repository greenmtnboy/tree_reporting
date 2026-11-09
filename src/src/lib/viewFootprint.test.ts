import { describe, expect, it } from 'vitest'
import { convexHull, insidePolygon, viewFootprint, FOOTPRINT_PAD_MAX_M, type Point } from './viewFootprint'

describe('viewFootprint', () => {
  // A camera at the origin looking north over a trapezoid 100-1000 m ahead.
  const ground: Point[] = [[-50, 100], [50, 100], [600, 1000], [-600, 1000]]

  it('keeps the view shape when unpadded, counter-clockwise', () => {
    const polygon = viewFootprint([0, 0], 0, ground, 0)
    expect(polygon).toHaveLength(4)
    expect(insidePolygon(polygon, [0, 500])).toBe(true)
    // Off-screen corners of the bounding box are out.
    expect(insidePolygon(polygon, [500, 150])).toBe(false)
    expect(insidePolygon(polygon, [0, 50])).toBe(false)
  })

  it('pads behind the camera and past each side, capped in real metres', () => {
    const polygon = viewFootprint([0, 0], 0, ground, 0.25)
    // 0.25 of a 900 m depth is 225 m, measured behind the camera: the near edge is 100 m ahead of it.
    expect(insidePolygon(polygon, [0, -200])).toBe(true)
    expect(insidePolygon(polygon, [0, -250])).toBe(false)
    expect(insidePolygon(polygon, [800, 1000])).toBe(true)
    const deep: Point[] = [[-50, 100], [50, 100], [6000, 10000], [-6000, 10000]]
    const capped = viewFootprint([0, 0], 0, deep, 0.25)
    expect(insidePolygon(capped, [0, -FOOTPRINT_PAD_MAX_M + 1])).toBe(true)
    expect(insidePolygon(capped, [0, -FOOTPRINT_PAD_MAX_M - 1])).toBe(false)
  })

  it('turns with the bearing', () => {
    // Looking east, the same trapezoid rotated: x forward, -y to the right... ground given in world coordinates.
    const east: Point[] = ground.map(([x, y]) => [y, -x])
    const polygon = viewFootprint([0, 0], 90, east, 0.25)
    expect(insidePolygon(polygon, [500, 0])).toBe(true)
    expect(insidePolygon(polygon, [-200, 0])).toBe(true)
    expect(insidePolygon(polygon, [-250, 0])).toBe(false)
  })

  it('hulls a degenerate set', () => {
    expect(convexHull([[0, 0], [1, 0], [1, 1], [0, 1], [0.5, 0.5]])).toEqual([[0, 0], [1, 0], [1, 1], [0, 1]])
  })
})
