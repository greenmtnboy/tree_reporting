export interface ResizeOptions {
  maxDim?: number
  quality?: number
  mimeType?: string
}

export async function resizeImage(
  source: File | Blob,
  options: ResizeOptions = {},
): Promise<Blob> {
  const { maxDim = 1600, quality = 0.85, mimeType = 'image/jpeg' } = options

  const bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' })
  try {
    const scale = Math.min(1, maxDim / Math.max(bitmap.width, bitmap.height))
    const targetW = Math.max(1, Math.round(bitmap.width * scale))
    const targetH = Math.max(1, Math.round(bitmap.height * scale))

    const canvas = document.createElement('canvas')
    canvas.width = targetW
    canvas.height = targetH
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('Could not get 2d canvas context')
    ctx.drawImage(bitmap, 0, 0, targetW, targetH)

    return await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error('Canvas toBlob failed'))),
        mimeType,
        quality,
      )
    })
  } finally {
    bitmap.close()
  }
}
