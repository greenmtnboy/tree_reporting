// Asymmetric saguaro study and a younger column, set among low sand ridges.
// Local templates share the same plant transform for outlines and details.
export const desertScene = {
  mature: { x: 315, y: 475, scale: 1 },
  young: { x: 191, y: 478, scale: 1 },
  duneOpacity: .32,
}

const point = xy => xy.map(value => Number(value.toFixed(2))).join(' ')
const cubic = (a, b, c, d, t) => a.map((value, axis) =>
  (1 - t) ** 3 * value + 3 * (1 - t) ** 2 * t * b[axis] + 3 * (1 - t) * t ** 2 * c[axis] + t ** 3 * d[axis])
const local = plant => ([x, y]) => [plant.x + x * plant.scale, plant.y + y * plant.scale]

// M/C/L path data also supplies sampled silhouettes for transparent occlusion.
function shape(commands, transform = xy => xy) {
  const polygon = []
  let current
  const path = commands.map(([command, ...values]) => {
    const points = Array.from({ length: values.length / 2 }, (_, i) => transform(values.slice(i * 2, i * 2 + 2)))
    if (command === 'C') {
      for (let step = 1; step <= 160; step++) polygon.push(cubic(current, ...points, step / 160))
    } else if (points.length) polygon.push(points[0])
    if (points.length) current = points.at(-1)
    return command + points.map(point).join(' ')
  }).join(' ')
  return { path, polygon }
}

const matureOutline = [
  ['M', -21, 3],
  ['C', -19, -67, -25, -108, -24, -130],
  ['C', -65, -131, -88, -142, -91, -171],
  ['C', -92, -189, -93, -210, -92, -224],
  ['C', -93, -243, -69, -247, -67, -225],
  ['C', -65, -210, -67, -191, -65, -185],
  ['C', -63, -170, -45, -165, -22, -166],
  ['C', -23, -207, -22, -276, -20, -307],
  ['C', -19, -332, -12, -345, -2, -346],
  ['C', 12, -347, 21, -332, 20, -311],
  ['C', 19, -281, 17, -238, 18, -211],
  ['C', 34, -212, 47, -225, 48, -245],
  ['C', 49, -255, 48, -266, 49, -273],
  ['C', 50, -293, 73, -294, 75, -273],
  ['C', 76, -260, 75, -246, 74, -235],
  ['C', 72, -193, 52, -175, 19, -173],
  ['C', 18, -126, 22, -73, 18, 1],
  ['Z'],
]
const youngOutline = [
  ['M', -14, 1], ['C', -16, -25, -21, -64, -17, -86],
  ['C', -13, -107, 6, -108, 12, -89],
  ['C', 18, -70, 13, -29, 14, 0], ['Z'],
]

function inside([x, y], polygon) {
  let contained = false
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i]
    const b = polygon[j]
    if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) contained = !contained
  }
  return contained
}

function dunes(silhouettes) {
  const curves = [
    [['M', 20, 353], ['C', 89, 342, 133, 290, 193, 302], ['C', 271, 318, 334, 372, 464, 335]],
    [['M', 3, 398], ['C', 109, 369, 175, 413, 260, 376], ['C', 332, 345, 391, 340, 464, 358]],
    [['M', 62, 427], ['C', 156, 389, 227, 416, 296, 427], ['C', 365, 439, 414, 404, 464, 391]],
    [['M', 10, 475], ['C', 110, 430, 204, 466, 280, 453], ['C', 353, 439, 408, 452, 464, 428]],
    [['M', 45, 499], ['C', 164, 464, 284, 505, 464, 473]],
  ]
  // Interrupt dunes behind the actual silhouettes, preserving transparency
  // without background-colored fills or SVG mask IDs shared by two previews.
  return curves.map(commands => {
    const samples = shape(commands).polygon
    let drawing = false
    return samples.map(p => {
      if (silhouettes.some(polygon => inside(p, polygon))) {
        drawing = false
        return ''
      }
      const command = drawing ? 'L' : 'M'
      drawing = true
      return command + point(p)
    }).join(' ')
  }).join(' ')
}

function matureDetails(plant) {
  const transform = local(plant)
  const ribs = []
  const spines = []
  for (const fraction of [-.65, -.32, 0, .32, .65]) {
    const ribPoint = y => transform([
      -1.5 + 2 * Math.sin((y + 346) / 346 * Math.PI) + fraction * 18 * Math.sqrt(Math.min(1, (y + 346) / 26)), y,
    ])
    const top = -340 + Math.abs(fraction) * 10
    const samples = Array.from({ length: 70 }, (_, i) => ribPoint(top + (-5 - top) * i / 69))
    ribs.push('M' + samples.map(point).join(' L'))
    for (let y = top + 15; y < -12; y += 23) {
      const [x, py] = ribPoint(y)
      const s = plant.scale
      spines.push(`M${point([x - 1.3 * s, py - 1.4 * s])} L${point([x + 1.1 * s, py + 1.2 * s])}`)
    }
  }
  const armRibs = [
    [['M', -85, -229], ['C', -85, -215, -86, -187, -83, -175], ['C', -79, -149, -55, -145, -29, -142]],
    [['M', -77, -231], ['C', -77, -210, -79, -191, -76, -181], ['C', -72, -159, -47, -154, -26, -154]],
    [['M', 57, -280], ['C', 56, -263, 59, -249, 55, -234], ['C', 50, -212, 40, -204, 24, -200]],
    [['M', 65, -282], ['C', 67, -264, 66, -244, 64, -234], ['C', 59, -205, 46, -190, 25, -186]],
  ]
  ribs.push(...armRibs.map(commands => shape(commands, transform).path))
  return { ribs: ribs.join(' '), spines: spines.join(' ') }
}

export function generateDesertSvg() {
  const mature = shape(matureOutline, local(desertScene.mature))
  const young = shape(youngOutline, local(desertScene.young))
  const details = matureDetails(desertScene.mature)
  const youngRibs = [-9, -3, 4, 9].map(x => shape([
    ['M', x * .4 - 3, -96 + Math.abs(x)], ['C', x - 4, -73, x - 1, -27, x, -4],
  ], local(desertScene.young)).path).join(' ')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 460 510" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round">
  <!-- Generated by generators/desert.mjs; edit the scene, then pnpm artwork:generate. -->
  <g class="dunes" opacity="${desertScene.duneOpacity}" stroke-width=".7"><path d="${dunes([mature.polygon, young.polygon])}" /></g>
  <g class="botanical">
    <g class="cacti" fill="currentColor" fill-opacity=".035"><path d="${mature.path.replace(/ Z$/, '')}" /><path d="${young.path.replace(/ Z$/, '')}" /></g>
    <g class="ribs" stroke-width=".7" opacity=".65"><path d="${details.ribs} ${youngRibs}" /></g>
    <g class="spines" stroke-width=".55" opacity=".5"><path d="${details.spines}" /></g>
    <g class="ground" stroke-width=".7" opacity=".5"><path d="M166 483q21 5 41-1 M281 482q28 8 65-1 M235 477l6-1m8 10 5 1m106-16 8-2m-206 22 5-1" /></g>
  </g>
</svg>
`
}
