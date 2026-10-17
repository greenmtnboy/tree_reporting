import city from './city.svg?raw'
import woodland from './woodland.svg?raw'
import broadleaf from './broadleaf.svg?raw'
import conifer from './conifer.svg?raw'
import desert from './desert.svg?raw'
import grassland from './grassland.svg?raw'
import tropical from './tropical.svg?raw'
import boreal from './boreal.svg?raw'
import mangrove from './mangrove.svg?raw'
import dryForest from './dry-forest.svg?raw'
import wetland from './wetland.svg?raw'
import montane from './montane.svg?raw'
import tundra from './tundra.svg?raw'

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
  tropical: {
    svg: tropical, title: 'Moist tropical forest', example: 'Bogotá · Taipei',
    motif: 'Pointed evergreen leaves above an arching understory fern',
    review: 'One entire-leaf template with drawn-out tips follows the terminal direction of each branch. Fern leaflets start on the curved rachis and taper toward its tip. These are shared moist-forest motifs, not a species portrait. Edit tropicalScene in generators/tropical.mjs, then run pnpm artwork:generate.',
  },
  boreal: {
    svg: boreal, title: 'Boreal forest & taiga', example: 'Helsinki',
    motif: 'Open pairs of pine needles above a small-leaved forest-floor spray',
    review: 'Needles grow in pairs from shared points on three curved shoots. Their open fans stay separate, with bare branch junctions and no cones. The lower spray uses small entire leaves attached by curved petioles. Edit borealScene in generators/boreal.mjs, then run pnpm artwork:generate.',
  },
  mangrove: {
    svg: mangrove, title: 'Mangroves', example: 'Tidal shores',
    motif: 'Leathery leaves above arching prop roots and tidal ripples',
    review: 'A red-mangrove-inspired sapling. Roots and branches attach to the same trunk curve; root forms vary among mangrove species. Edit mangroveScene in generators/habitat-studies.mjs.',
  },
  'dry-forest': {
    svg: dryForest, title: 'Dry tropical forest', example: 'Seasonal tropical forests',
    motif: 'Open deciduous branching, sparse broad leaves, and fallen foliage',
    review: 'A dry-season study with exposed twigs and a few retained leaves. It replaces the moist-forest fern motif. Edit dryForestScene in generators/habitat-studies.mjs.',
  },
  wetland: {
    svg: wetland, title: 'Flooded grasslands', example: 'Marshes and flooded savannas',
    motif: 'Emergent reeds, sedges, and broken water lines',
    review: 'A representative marsh margin, with reed heads and long leaves attached to curved stalks. Edit wetlandScene in generators/habitat-studies.mjs.',
  },
  montane: {
    svg: montane, title: 'Montane grasslands', example: 'High-elevation grasslands and shrublands',
    motif: 'Coarse tussocks among angular stones and mountain contours',
    review: 'A high-elevation tussock study, separated from lowland seed-bearing grasses. Edit montaneScene in generators/habitat-studies.mjs.',
  },
  tundra: {
    svg: tundra, title: 'Tundra', example: 'Low-growing open tundra',
    motif: 'Low branching small-leaved shrub, cottony sedge heads, and lichen',
    review: 'A compact forked shrub with short petioles, alternating leaves, and leafy shoot tips beside short sedges. Edit tundraScene in generators/habitat-studies.mjs.',
  },
  city: {
    svg: city, title: 'City geometry', example: 'Shared upper-left drawing',
    motif: 'Four buildings, a wandering street, and little planted pockets',
    review: 'Buildings share a ground plane and consistent facade geometry. Rounded curbs, dashed lanes, a small crossing, two pocket gardens, and a loose second pencil pass soften the streets. Edit cityScene in generators/city.mjs; street variation is deterministic.',
  },
} as const

export type FieldSketchName = keyof typeof fieldSketches
