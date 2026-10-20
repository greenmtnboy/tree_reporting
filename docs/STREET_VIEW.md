# Tree card Street View

The tree card opens a modal with the nearest outdoor Google Street View panorama
within 100 metres, initially facing the selected tree. Its minimap marks the tree
in green and the camera as a blue arrow; moving or turning in Street View updates
the minimap. “Face tree” restores the bearing toward the tree. Imagery can predate
the tree and is not itself a check-in or confirmation.

The GitHub Pages build reads `VITE_GOOGLE_MAPS_API_KEY` from the `deploy`
environment's secret (repository secrets also work). Vite embeds this browser key
at build time. Enable the Maps JavaScript API and billing on its Google Cloud
project, and restrict the key to the production website's HTTP referrers and the
Maps JavaScript API. A change to the secret requires a new build. For local live
verification, provide the variable in an untracked `src/.env.local` with an
appropriately restricted development key.

No Google script or panorama is loaded until the visitor opens Street View.
Missing configuration, script loading failures, and unavailable imagery display a
message and a link to Google Maps. The native modal provides focus containment,
Escape to close, and focus restoration. Closing during asynchronous loading
prevents a late viewer from being created.

The ghost-tree SVG is bundled by Vite from
`src/src/artwork/field-sketches/ghost-tree.svg`; no GCS object or output-table URL
is needed. It is an unidentified-tree illustration, not a picture of that tree.
Visitor and submission photos retain priority.

Prediction notes use `PREDICTED_*` sources only, and only after a successful
check-in count read reports zero. Unknown counts (loading, offline, or missing
Firebase configuration) do not imply zero. Reviewed `SATELLITE_*` records and
predictions with check-ins never display the note.

API reference: https://developers.google.com/maps/documentation/javascript/streetview
