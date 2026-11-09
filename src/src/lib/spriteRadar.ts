import type { SpriteRadar } from '../composables/treeCrownLayer'
import type { Point } from './viewFootprint'
import { lngLatFrom3857 } from './treeDots'

/**
 * Draws the debug radar: a top-down plot centred on the camera with its heading up. It shows
 * the ground on screen (white), the installed sprite batch's fetch area (green) and how far a
 * capped batch reached (orange ring around the camera it was queried from), the query in
 * flight (blue, dashed), and a sample of the drawn trees.
 */
export function drawSpriteRadar(canvas: HTMLCanvasElement, radar: SpriteRadar) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const dpr = window.devicePixelRatio || 1
  const size = canvas.clientWidth
  if (canvas.width !== Math.round(size * dpr)) { canvas.width = Math.round(size * dpr); canvas.height = Math.round(size * dpr) }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, size, size)

  const b = radar.bearing * Math.PI / 180
  const f: Point = [Math.sin(b), Math.cos(b)], r: Point = [Math.cos(b), -Math.sin(b)]
  const rel = ([x, y]: Point): Point => {
    const dx = x - radar.eye[0], dy = y - radar.eye[1]
    return [dx * r[0] + dy * r[1], dx * f[0] + dy * f[1]]
  }
  // The camera sits low, heading up; fit what is ahead, behind and to each side separately.
  const shapes = [radar.view, radar.fetched ?? [], radar.pending ?? []].flat().map(rel)
  const half = size / 2, cx = half, cy = size * 0.78, margin = 6
  const ahead = Math.max(1, ...shapes.map(([, v]) => v)), behind = Math.max(1, ...shapes.map(([, v]) => -v))
  const side = Math.max(1, ...shapes.map(([u]) => Math.abs(u)))
  const scale = Math.min((cy - margin) / ahead, (size - cy - margin) / behind, (half - margin) / side)
  const extent = (cy - margin) / scale
  const screen = ([x, y]: Point): Point => { const [u, v] = rel([x, y]); return [cx + u * scale, cy - v * scale] }
  const path = (polygon: Point[]) => {
    ctx.beginPath()
    polygon.map(screen).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)))
    ctx.closePath()
  }

  ctx.fillStyle = 'rgba(0, 0, 0, 0.55)'
  ctx.fillRect(0, 0, size, size)
  if (radar.fetched) {
    path(radar.fetched)
    ctx.fillStyle = 'rgba(102, 187, 106, 0.18)'
    ctx.fill()
    ctx.strokeStyle = 'rgba(102, 187, 106, 0.9)'
    ctx.lineWidth = 1
    ctx.stroke()
  }
  ctx.fillStyle = 'rgba(165, 214, 167, 0.8)'
  for (let i = 0; i < radar.trees.length; i += 2) {
    const [x, y] = screen([radar.trees[i], radar.trees[i + 1]])
    ctx.fillRect(x, y, 1, 1)
  }
  if (radar.queryEye && Number.isFinite(radar.reach)) {
    const [x, y] = screen(radar.queryEye)
    ctx.beginPath()
    ctx.arc(x, y, radar.reach * scale, 0, Math.PI * 2)
    ctx.strokeStyle = 'rgba(255, 167, 38, 0.9)'
    ctx.stroke()
  }
  if (radar.pending) {
    path(radar.pending)
    ctx.setLineDash([3, 3])
    ctx.strokeStyle = 'rgba(100, 181, 246, 0.95)'
    ctx.stroke()
    ctx.setLineDash([])
  }
  path(radar.view)
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.9)'
  ctx.stroke()
  // The camera, heading up.
  ctx.beginPath()
  ctx.moveTo(cx, cy - 5)
  ctx.lineTo(cx - 3.5, cy + 3)
  ctx.lineTo(cx + 3.5, cy + 3)
  ctx.closePath()
  ctx.fillStyle = '#fff'
  ctx.fill()
  ctx.font = '10px ui-monospace, monospace'
  ctx.fillStyle = 'rgba(255, 255, 255, 0.75)'
  // EPSG:3857 metres are 1 / cos(lat) real ones.
  const real = extent * Math.cos(lngLatFrom3857(radar.eye[0], radar.eye[1])[1] * Math.PI / 180)
  ctx.fillText(`${(real / 1000).toFixed(1)} km`, 4, size - 4)
}
