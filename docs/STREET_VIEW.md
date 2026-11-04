# Tree card Street View

Street View loads the Maps JavaScript API on demand. The inset marks the tree
in green and the actual panorama camera in blue, with an arrow that tracks its
heading as visitors turn or navigate. The map fits both locations. Full screen
expands the entire workspace: side by side on desktop and stacked on phones.
Closing restores focus and releases the maps, observers and panorama listeners.

Move tree / set species and Report missing tree open the existing reviewed
correction form alongside the panorama. These are remote suggestions, not visits
or check-ins. Imagery can predate a tree; reports are reviewed before publication.

## Configuration and billing

The build reads `VITE_GOOGLE_MAPS_API_KEY`. Enable **Maps JavaScript API** on its
Google Cloud project and in the key's API restrictions. An Embed-only key will
no longer suffice. Restrict HTTP referrers to the deployed website (and use a
separate development key for localhost). Key changes require a rebuild.

Interactive panoramas incur Google's Dynamic Street View usage charges; configure
billing quotas and alerts for the project. The inset remains MapLibre/CARTO.
See https://developers.google.com/maps/documentation/javascript/usage-and-billing.

Missing configuration, failed loading and unavailable imagery have status messages
and an external Google Maps link. Google may display key/billing errors inside
its panorama. No live Google key is required by the mocked component tests.

Unknown species display a muted text question mark in the same responsive space
as a tree photo, with “Species not recorded” beneath it. No image asset, GCS
object, or output-table URL is needed. Visitor and submission photos retain
priority.

Prediction notes use `PREDICTED_*` sources only, and only after a successful
check-in count read reports zero. Unknown counts (loading, offline, or missing
Firebase configuration) do not imply zero. Reviewed `SATELLITE_*` records and
predictions with check-ins never display the note.

API reference: https://developers.google.com/maps/documentation/javascript/streetview
