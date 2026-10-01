import type { TreeForm } from '../types'
import { CATEGORY_COLORS } from '../treeFormColors'
import { treeArtwork, fillTreeShape, TREE_TRUNK_COLOR, TREE_BLOOM_COLOR } from '../lib/treeArtwork'

export const TREE_ICON_SIZE = 48

interface CategoryInfo {
  category: TreeForm
  color: string
  label: string
}

const GENUS_TO_CATEGORY: Record<string, TreeForm> = {
  washingtonia: 'palm',
  lophostemon: 'broadleaf',
  pittosporum: 'broadleaf',
  ulmus: 'broadleaf',
  magnolia: 'broadleaf',
  ligustrum: 'broadleaf',
  olea: 'broadleaf',
  ginkgo: 'broadleaf',
  acer: 'broadleaf',
  platanus: 'spreading',
  acacia: 'spreading',
  callistemon: 'weeping',
  melaleuca: 'multi_trunk',
  metrosideros: 'spreading',
  tristaniopsis: 'columnar',
  tristania: 'columnar',
  geijera: 'columnar',
  prunus: 'ornamental',
  pyrus: 'ornamental',
  ceanothus: 'ornamental',
  dodonaea: 'ornamental',
  hymenosporum: 'ornamental',
  myoporum: 'broadleaf',
  cupressus: 'conifer',
  salix: 'weeping',
  lagerstroemia: 'multi_trunk',
}

export { CATEGORY_COLORS }

export const CATEGORY_LABELS: Record<TreeForm, string> = {
  palm: 'Palm',
  broadleaf: 'Broadleaf',
  conifer: 'Conifer',
  columnar: 'Columnar',
  ornamental: 'Ornamental',
  spreading: 'Spreading',
  weeping: 'Weeping',
  multi_trunk: 'Multi-trunk',
  default: 'Other',
}

export function getTreeForm(qSpecies: string): CategoryInfo {
  const genus = qSpecies.split('::')[0].trim().split(' ')[0].toLowerCase()
  const category = GENUS_TO_CATEGORY[genus] ?? 'default'
  return {
    category,
    color: CATEGORY_COLORS[category],
    label: CATEGORY_LABELS[category],
  }
}

/** Generate a canvas image for a tree category silhouette */
export function drawTreeIcon(category: TreeForm, size: number, color?: string): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  color = color ?? CATEGORY_COLORS[category]
  const artwork = treeArtwork(category)
  for (const [shapes, fill] of [[artwork.trunk, TREE_TRUNK_COLOR], [artwork.foliage, color], [artwork.blooms, TREE_BLOOM_COLOR]] as const) {
    ctx.fillStyle = fill
    for (const shape of shapes) fillTreeShape(ctx, shape, size)
  }
  // Faint outline around the whole tree silhouette
  ctx.globalCompositeOperation = 'source-over'
  const outlineData = ctx.getImageData(0, 0, size, size)
  const od = outlineData.data
  // Draw a 1px stroke around non-transparent pixels
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.25)'
  ctx.lineWidth = 1.5
  ctx.globalCompositeOperation = 'destination-over'
  for (let y = 1; y < size - 1; y++) {
    for (let x = 1; x < size - 1; x++) {
      const i = (y * size + x) * 4
      if (od[i + 3] > 0) {
        // Check if any neighbor is transparent (edge pixel)
        const neighbors = [
          ((y - 1) * size + x) * 4,
          ((y + 1) * size + x) * 4,
          (y * size + x - 1) * 4,
          (y * size + x + 1) * 4,
        ]
        for (const ni of neighbors) {
          if (od[ni + 3] === 0) {
            ctx.fillStyle = 'rgba(255, 255, 255, 0.3)'
            ctx.fillRect(x - 0.5, y - 0.5, 1.5, 1.5)
            break
          }
        }
      }
    }
  }

  return canvas
}

const ALL_CATEGORIES: TreeForm[] = [
  'broadleaf',
  'conifer',
  'palm',
  'columnar',
  'ornamental',
  'spreading',
  'weeping',
  'multi_trunk',
  'default',
]

/**
 * Register category-shaped icons for each given hex color.
 * Image names are `tree-{category}-{hex}` (e.g. `tree-broadleaf-#4CAF50`).
 * This is also used for the default category colors so the pipeline is uniform.
 */
export function registerCategoryColoredIcons(map: maplibregl.Map, hexColors: string[], size = TREE_ICON_SIZE): void {
  for (const hex of hexColors) {
    for (const cat of ALL_CATEGORIES) {
      const imageName = `tree-${cat}-${hex}`
      if (map.hasImage(imageName)) continue
      try {
        const canvas = drawTreeIcon(cat, size, hex)
        const ctx = canvas.getContext('2d')
        if (!ctx) continue
        const imageData = ctx.getImageData(0, 0, size, size)
        map.addImage(imageName, {
          width: size,
          height: size,
          data: new Uint8Array(imageData.data.buffer),
        })
      } catch (e) {
        console.warn('[TreeIcons] failed to register colored category icon', {
          cat,
          hex,
          error: (e as Error)?.message ?? String(e),
        })
      }
    }
  }
}
