# Tree card Street View

The tree card opens a Google Maps Embed API iframe requesting the nearest outdoor
Street View panorama within 100 metres. Visitors can look around inside the
iframe. A separate, non-interactive MapLibre inset uses the app's existing CARTO
basemap and marks the selected tree. It does not track the Street View camera.
The iframe does not expose camera coordinates or direction, so automatic
“Face tree”, live camera arrows, and synchronized minimap movement are unavailable.
No heading is guessed. Imagery can predate the tree and is not a confirmation.

## Configuration and billing

The GitHub Pages build still reads `VITE_GOOGLE_MAPS_API_KEY` from the `deploy`
environment's secret (repository secrets also work). Enable **Maps Embed API** in
the project that owns this key and allow **Maps Embed API** in the key's API
restrictions. This feature no longer needs Maps JavaScript API or Street View
Static API. Remove those from this key's allowed APIs if nothing else uses them.

Restrict the key to the website's HTTP referrers, e.g.
`https://greenmtnboy.github.io/*`. The iframe explicitly uses
`strict-origin-when-cross-origin`, which sends the origin for key validation.
The existing secret can be retained; changing its value requires a rebuild.
For local live verification, use an untracked `src/.env.local` and a development
key restricted to the local website origin.

Maps Embed API currently has unlimited no-charge usage. The application creates
no Google Maps JavaScript map or panorama instances and makes no metadata API
requests. The inset uses the same basemap provider as the main map.
See https://developers.google.com/maps/documentation/embed/usage-and-billing.

## Loading and limitations

Google content loads only when the visitor opens the dialog. Missing configuration
shows a message and an external Google Maps link. Google displays API-key and
imagery errors inside its iframe; the parent cannot inspect that cross-origin
content, and an iframe load event does not prove that imagery succeeded. The
external link remains available for unavailable or blocked embeds. Closing removes
the iframe and releases the inset map. The native modal provides focus containment,
Escape to close, and focus restoration.

Unknown species display a muted text question mark in the same responsive space
as a tree photo, with “Species not recorded” beneath it. No image asset, GCS
object, or output-table URL is needed. Visitor and submission photos retain
priority.

Prediction notes use `PREDICTED_*` sources only, and only after a successful
check-in count read reports zero. Unknown counts (loading, offline, or missing
Firebase configuration) do not imply zero. Reviewed `SATELLITE_*` records and
predictions with check-ins never display the note.

API reference: https://developers.google.com/maps/documentation/embed/embedding-map
