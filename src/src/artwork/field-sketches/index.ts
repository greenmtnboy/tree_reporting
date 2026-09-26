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
    motif: 'A branching spray of narrow leaves',
    review: 'Start here: several leaf bases and side branches miss the curved main stem. Rebuild them from shared attachment points; add restrained secondary veins.',
  },
  broadleaf: {
    svg: broadleaf, title: 'Broadleaf forest', example: 'Boston · London',
    motif: 'Lobed leaves and a branching twig',
    review: 'The rotated upper leaf study and the main twig were drawn separately. Check that petioles meet the twig, and make the smaller leaves feel like a coherent botanical study.',
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
    review: 'Seed heads and stems use different curves, leaving some attachments loose. Join them consistently and vary their spacing and silhouette.',
  },
  city: {
    svg: city, title: 'City geometry', example: 'Shared upper-left drawing',
    motif: 'Four buildings, a wandering street, and little planted pockets',
    review: 'Buildings share a ground plane and consistent facade geometry. Rounded curbs, dashed lanes, a small crossing, two pocket gardens, and a loose second pencil pass soften the streets. Edit cityScene in generators/city.mjs; street variation is deterministic.',
  },
} as const

export type FieldSketchName = keyof typeof fieldSketches
