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
    motif: 'Needles, branching twigs, and a cone',
    review: 'Needles were spaced along straight lines while the stem curves. Anchor each cluster to its twig and refine the cone attachment and overlapping scales.',
  },
  desert: {
    svg: desert, title: 'Desert & xeric scrub', example: 'Tempe',
    motif: 'An upright cactus and a low succulent',
    review: 'Refine the cactus ribs and succulent leaf tips. Decide whether these broad regional motifs are specific enough for the city’s ecosystem.',
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
