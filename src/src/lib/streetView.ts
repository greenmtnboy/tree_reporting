export interface TreePosition { lat: number; lng: number }

/** Embed selects nearby outdoor imagery; its camera position stays inside the iframe. */
export function streetViewEmbedUrl(position: TreePosition, key: string | undefined): string | null {
  if (!key?.trim()) return null
  const params = new URLSearchParams({
    key: key.trim(),
    location: `${position.lat},${position.lng}`,
    radius: '100',
    source: 'outdoor',
  })
  // Do not invent a heading: Embed does not return the selected camera location.
  return `https://www.google.com/maps/embed/v1/streetview?${params}`
}

export function streetViewExternalUrl(position: TreePosition): string {
  return `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${position.lat},${position.lng}`
}
