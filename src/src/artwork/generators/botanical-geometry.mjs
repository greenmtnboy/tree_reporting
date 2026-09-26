// Shared construction for the new studies: attached parts use the same
// quadratic curve and local tangent, never separately guessed coordinates.
export const point = xy => xy.map(value => Number(value.toFixed(3))).join(' ')
export const onCurve = ({ base, bend, tip }, t) => base.map((value, axis) =>
  (1 - t) ** 2 * value + 2 * t * (1 - t) * bend[axis] + t ** 2 * tip[axis])
export const tangent = ({ base, bend, tip }, t) => base.map((value, axis) =>
  2 * (1 - t) * (bend[axis] - value) + 2 * t * (tip[axis] - bend[axis]))
export const stem = ({ base, bend, tip }) => `M${point(base)} Q${point(bend)} ${point(tip)}`

export function frame(origin, direction) {
  const magnitude = Math.hypot(...direction)
  const [dx, dy] = direction.map(value => value / magnitude)
  return (across, forward) => [
    origin[0] - dy * across + dx * forward,
    origin[1] + dx * across + dy * forward,
  ]
}

export function simpleLeaf(curve, length, width, drip = false) {
  const position = frame(curve.tip, tangent(curve, 1))
  const local = (x, y) => point(position(x * width, y * length))
  const shoulder = drip ? .83 : .94
  const outline = `M${local(0, 0)} C${local(-.64, .08)} ${local(-1.18, .42)} ${local(-.53, .7)} C${local(-.2, shoulder)} ${local(-.07, .94)} ${local(0, 1)} C${local(.09, .92)} ${local(.25, shoulder)} ${local(.61, .68)} C${local(1.06, .4)} ${local(.65, .06)} ${local(0, 0)} Z`
  const veins = [.18, .32, .46, .60].flatMap(t => [-1, 1].map(side =>
    `M${local(0, t)} Q${local(side * .20, t + .08)} ${local(side * (.47 - Math.abs(t - .30) * .8), t + .17)}`)).join(' ')
  return { outline, midrib: `M${local(0, 0)} L${local(0, 1)}`, veins }
}
