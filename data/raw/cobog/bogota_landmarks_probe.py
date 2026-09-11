#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""Freshness probe for Bogotá's monuments and heritage buildings.

Neither layer publishes `editingInfo` or an edit-date column, so the watermark
is the catalogue's: the later of the two datasets' publication stamps on
`datosabiertos.bogota.gov.co`, read through `_ckan_shared.data_last_modified`
against each dataset's "Esri REST" resource.  Those resources carry no
`last_modified` of their own, so the helper falls back to the package's
`metadata_modified` -- which moves for a description edit as well as for
data.  That is the right trade for a weekly lane over ~1,500 rows, and the
alternative is no watermark at all.

The later of the two, because the script unions both and either can move on
its own.
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ckan_shared import CkanResource, data_last_modified
from _ingest_shared import emit_freshness

RESOURCES = (
    # Monumentos - Inventario Patrimonio Mueble, the "Esri REST" resource
    CkanResource("datosabiertos.bogota.gov.co", "ede720ba-d6ee-42de-b8a1-0dca65f7cec6"),
    # Bienes Inmuebles de Interés Cultural, the "ESRI REST" resource
    CkanResource("datosabiertos.bogota.gov.co", "30295b54-05f7-4136-a8a2-98ee3a947f0f"),
)


def fetch_modified_at() -> datetime:
    return max(data_last_modified(resource) for resource in RESOURCES)


if __name__ == "__main__":
    emit_freshness("COBOG", fetch_modified_at, label="COBOG landmarks")
