import city from './city.svg?raw'
import woodland from './woodland.svg?raw'
import broadleaf from './broadleaf.svg?raw'
import conifer from './conifer.svg?raw'
import desert from './desert.svg?raw'
import grassland from './grassland.svg?raw'

/** Static, repository-owned SVG only. Never add user-supplied markup here. */
export const fieldSketches = {
  woodland: {
    svg: woodland, title: 'Mediterranean woodland', example: 'San Francisco · Athens',
    motif: 'A branching olive spray with narrow leaves and paired fruit',
    review: 'Olive-inspired leaves, side branches, and fruit stems share exact attachment points on the curved stalk. Leaf angles follow its local direction; rounded olives hang in two small pairs. Edit woodlandScene in generators/woodland.mjs, then run pnpm artwork:generate.',
  },
  broadleaf: {
    svg: broadleaf, title: 'Broadleaf forest', example: 'Boston · London',
    motif: 'One oak-like leaf type on an alternating spray',
    review: 'Every branch begins on the curved main twig. Each oak-like leaf shares one rounded-lobe template, meets its branch tip, and follows its direction; veins join the same midrib. Edit broadleafScene in generators/broadleaf.mjs, then run pnpm artwork:generate.',
  },
  conifer: {
    svg: conifer, title: 'Conifer forest', example: 'Vancouver',
    motif: 'An airy fir-like sprig with five tapered needle fans',
    review: 'Five separated needle fans follow the curved leader and four side branches. Short, tapered needles leave space around the branch junctions. Edit coniferScene in generators/conifer.mjs, then run pnpm artwork:generate.',
  },
  desert: {
    svg: desert, title: 'Desert & xeric scrub', example: 'Tempe',
    motif: 'An asymmetric saguaro, a young column, and low sand ridges',
    review: 'Rounded, unequal arms merge into a gently irregular trunk, with fine ribs and restrained spine marks. Five dune contours pass behind the cacti. Edit desertScene in generators/desert.mjs, then run pnpm artwork:generate.',
  },
  grassland: {
    svg: grassland, title: 'Grassland & savanna', example: 'Buenos Aires',
    motif: 'Arching grasses and seed heads',
    review: 'Each leaf starts on its curved stalk and rotates with the local stalk angle. Seed heads are evenly spaced along the curve. Edit grasslandScene in generators/grassland.mjs, then run pnpm artwork:generate; grassland and savanna share this drawing.',
  },
  city: {
    svg: city, title: 'City geometry', example: 'Shared upper-left drawing',
    motif: 'Four buildings, a wandering street, and little planted pockets',
    review: 'Buildings share a ground plane and consistent facade geometry. Rounded curbs, dashed lanes, a small crossing, two pocket gardens, and a loose second pencil pass soften the streets. Edit cityScene in generators/city.mjs; street variation is deterministic.',
  },
} as const

export type FieldSketchName = keyof typeof fieldSketches
