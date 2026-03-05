import type { TreeCategory } from '../types'
import type { CategoryIconData } from './useTreeData'

interface CategoryInfo {
  category: TreeCategory
  color: string
  label: string
}

const GENUS_TO_CATEGORY: Record<string, TreeCategory> = {
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
  callistemon: 'coniferous',
  melaleuca: 'coniferous',
  metrosideros: 'coniferous',
  tristaniopsis: 'columnar',
  tristania: 'columnar',
  geijera: 'columnar',
  prunus: 'ornamental',
  pyrus: 'ornamental',
  ceanothus: 'ornamental',
  dodonaea: 'ornamental',
  hymenosporum: 'ornamental',
  myoporum: 'broadleaf',
  cupressus: 'coniferous',
}

export const CATEGORY_COLORS: Record<TreeCategory, string> = {
  palm: '#e6a835',
  broadleaf: '#4CAF50',
  spreading: '#8BC34A',
  coniferous: '#2E7D32',
  columnar: '#43A047',
  ornamental: '#E91E63',
  default: '#66BB6A',
}

export const CATEGORY_LABELS: Record<TreeCategory, string> = {
  palm: 'Palm',
  broadleaf: 'Broadleaf',
  spreading: 'Spreading',
  coniferous: 'Coniferous',
  columnar: 'Columnar',
  ornamental: 'Ornamental',
  default: 'Other',
}

export function getTreeCategory(qSpecies: string): CategoryInfo {
  const genus = qSpecies.split('::')[0].trim().split(' ')[0].toLowerCase()
  const category = GENUS_TO_CATEGORY[genus] ?? 'default'
  return {
    category,
    color: CATEGORY_COLORS[category],
    label: CATEGORY_LABELS[category],
  }
}

/** Generate a canvas image for a tree category silhouette */
function drawTreeIcon(category: TreeCategory, size: number, color?: string): HTMLCanvasElement {
  const canvas = document.createElement('canvas')
  canvas.width = size
  canvas.height = size
  const ctx = canvas.getContext('2d')!
  color = color ?? CATEGORY_COLORS[category]
  const cx = size / 2
  const _bottom = size - 2

  ctx.fillStyle = '#5D4037'
  ctx.strokeStyle = 'none'

  switch (category) {
    case 'palm': {
      // Tall trunk with fan fronds at top
      const trunkW = size * 0.08
      ctx.fillRect(cx - trunkW / 2, size * 0.35, trunkW, size * 0.6)
      ctx.fillStyle = color
      // Fronds radiating from top
      for (let angle = -70; angle <= 70; angle += 28) {
        ctx.save()
        ctx.translate(cx, size * 0.35)
        ctx.rotate((angle * Math.PI) / 180)
        ctx.beginPath()
        ctx.ellipse(0, -size * 0.22, size * 0.08, size * 0.25, 0, 0, Math.PI * 2)
        ctx.fill()
        ctx.restore()
      }
      break
    }
    case 'broadleaf': {
      // Round canopy on a short trunk
      const trunkW = size * 0.1
      ctx.fillRect(cx - trunkW / 2, size * 0.55, trunkW, size * 0.4)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(cx, size * 0.38, size * 0.32, 0, Math.PI * 2)
      ctx.fill()
      break
    }
    case 'spreading': {
      // Wide flat canopy
      const trunkW = size * 0.1
      ctx.fillRect(cx - trunkW / 2, size * 0.5, trunkW, size * 0.45)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.ellipse(cx, size * 0.38, size * 0.42, size * 0.22, 0, 0, Math.PI * 2)
      ctx.fill()
      break
    }
    case 'coniferous': {
      // Sharp Christmas tree / conifer shape
      const trunkW = size * 0.08
      ctx.fillRect(cx - trunkW / 2, size * 0.7, trunkW, size * 0.25)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.moveTo(cx, size * 0.08)
      ctx.lineTo(cx + size * 0.28, size * 0.72)
      ctx.lineTo(cx - size * 0.28, size * 0.72)
      ctx.closePath()
      ctx.fill()
      break
    }
    case 'columnar': {
      // Rounded narrow / columnar deciduous (e.g. Australian Willow)
      const trunkW = size * 0.08
      ctx.fillRect(cx - trunkW / 2, size * 0.6, trunkW, size * 0.35)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.ellipse(cx, size * 0.38, size * 0.2, size * 0.32, 0, 0, Math.PI * 2)
      ctx.fill()
      break
    }
    case 'ornamental': {
      // Small rounded tree with visible bloom dots
      const trunkW = size * 0.08
      ctx.fillRect(cx - trunkW / 2, size * 0.55, trunkW, size * 0.4)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(cx, size * 0.4, size * 0.26, 0, Math.PI * 2)
      ctx.fill()
      // Bloom highlights
      ctx.fillStyle = '#F8BBD0'
      for (const [ox, oy] of [[-0.1, -0.08], [0.12, 0.04], [-0.04, 0.1], [0.08, -0.12]]) {
        ctx.beginPath()
        ctx.arc(cx + size * ox, size * 0.4 + size * oy, size * 0.05, 0, Math.PI * 2)
        ctx.fill()
      }
      break
    }
    default: {
      // Generic round tree
      const trunkW = size * 0.1
      ctx.fillRect(cx - trunkW / 2, size * 0.55, trunkW, size * 0.4)
      ctx.fillStyle = color
      ctx.beginPath()
      ctx.arc(cx, size * 0.38, size * 0.3, 0, Math.PI * 2)
      ctx.fill()
      break
    }
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

const ALL_CATEGORIES: TreeCategory[] = ['palm', 'broadleaf', 'spreading', 'coniferous', 'columnar', 'ornamental', 'default']

function decodeBase64ToU8(base64: string): Uint8Array {
  const bin = atob(base64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) {
    bytes[i] = bin.charCodeAt(i)
  }
  return bytes
}

/**
 * Register category-shaped icons for each given hex color.
 * Image names are `tree-{category}-{hex}` (e.g. `tree-broadleaf-#4CAF50`).
 * This is also used for the default category colors so the pipeline is uniform.
 */
export function registerCategoryColoredIcons(map: maplibregl.Map, hexColors: string[], size = 48): void {
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

/** Register all tree icons with a MapLibre map instance */
export function registerTreeIcons(map: maplibregl.Map, categoryIcons: CategoryIconData[] = [], size = 48): void {
  // Prefer enriched, pre-baked RGBA icons when available
  for (const icon of categoryIcons) {
    const imageName = `tree-${icon.category}`
    try {
      const decoded = decodeBase64ToU8(icon.rgbaBase64)
      const expectedLen = icon.width * icon.height * 4
      if (decoded.length !== expectedLen) continue
      const image = {
        width: icon.width,
        height: icon.height,
        data: decoded,
      }
      if (map.hasImage(imageName)) {
        map.updateImage(imageName, image)
      } else {
        map.addImage(imageName, image)
      }
    } catch (e) {
      console.warn('[TreeIcons] failed to register enriched icon', {
        category: icon.category,
        error: (e as Error)?.message ?? String(e),
      })
    }
  }

  // Fallback icon generation for any missing category icon
  for (const cat of ALL_CATEGORIES) {
    if (map.hasImage(`tree-${cat}`)) continue
    try {
      const canvas = drawTreeIcon(cat, size)
      const ctx = canvas.getContext('2d')
      if (!ctx) continue
      const imageData = ctx.getImageData(0, 0, size, size)
      map.addImage(`tree-${cat}`, {
        width: size,
        height: size,
        data: new Uint8Array(imageData.data.buffer),
      })
    } catch (e) {
      console.warn('[TreeIcons] failed to register fallback icon', {
        category: cat,
        error: (e as Error)?.message ?? String(e),
      })
    }
  }
}
