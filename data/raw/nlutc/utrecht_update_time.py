#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Utrecht's tree inventory (the bomenkaart).

The later of the two layers' `editingInfo.dataLastEditDate` (Esri epoch
milliseconds, UTC).  The hosted layers are overwritten by the city's quarterly
republish and edited by nobody in between, so the stamp moves exactly when
new data lands (2026-10-09 when this was written, the "laatst bijgewerkt"
date on the catalogue record).

**A renamed service raises.**  Utrecht has renamed the service before
(`Bomenkaart` -> `Bomenkaart_update_2024` -> `bomenkaart_2026`), and a deleted
ArcGIS service answers with an error envelope that the retry helper would
read as an outage, degrading this city to "no new data" for ever.  So the
probe first reads the city's umbrella "Bomen" record on data.overheid.nl
(the national catalogue harvesting data.utrecht.nl), whose "WFS actuele
bomenkaart" link is maintained to point at the current map, and raises when
it names a service other than the one the ingest reads.  Repoint `SERVICE`
in both scripts (the ids, `Boomnummer`, survive a rename).  The catalogue
being unreachable only skips that check.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, layer_last_edit
from shared.ingest import UpstreamUnavailable, emit_freshness, get_json_with_retry

BASE = "https://services-eu1.arcgis.com/SMnoOtmU2UWf0vRp/arcgis/rest/services"
# Must match utrecht_tree_info.py's SERVICE.
SERVICE = "bomenkaart_2026"
LAYERS = (
    FeatureLayer(f"{BASE}/{SERVICE}/FeatureServer/0"),
    FeatureLayer(f"{BASE}/{SERVICE}/FeatureServer/1"),
)

CATALOGUE_URL = "https://data.overheid.nl/data/api/3/action/package_show"
CATALOGUE_ID = "utrecht-bomen"
CURRENT_SERVICE = re.compile(r"/arcgis/services/([^/]+)/WFSServer", re.IGNORECASE)


def catalogued_service() -> str | None:
    """The service the catalogue's "actuele bomenkaart" link names, or None if unreachable."""
    try:
        payload = get_json_with_retry(CATALOGUE_URL, params={"id": CATALOGUE_ID}, timeout=60)
    except UpstreamUnavailable as exc:
        print(f"NLUTC probe: catalogue unreachable, rename check skipped ({exc})"[:300], file=sys.stderr)
        return None
    resources = (payload.get("result") or {}).get("resources")
    if not isinstance(resources, list):
        raise RuntimeError(f"{CATALOGUE_ID}: package_show returned no resources")
    names = {
        match.group(1)
        for resource in resources
        if (match := CURRENT_SERVICE.search(str(resource.get("url") or "")))
    }
    if len(names) != 1:
        raise RuntimeError(
            f"{CATALOGUE_ID}: expected one 'WFS actuele bomenkaart' service link, found {sorted(names)}"
        )
    return names.pop()


def fetch_modified_at() -> datetime:
    current = catalogued_service()
    if current is not None and current.lower() != SERVICE.lower():
        raise RuntimeError(
            f"Utrecht's current tree map is now the {current!r} service, not {SERVICE!r}; "
            "repoint SERVICE in nlutc/utrecht_tree_info.py and utrecht_update_time.py"
        )
    return max(layer_last_edit(layer) for layer in LAYERS)


if __name__ == "__main__":
    emit_freshness("NLUTC", fetch_modified_at)
