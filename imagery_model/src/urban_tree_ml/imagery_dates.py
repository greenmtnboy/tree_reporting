"""Resolve acquisition days from the actual selected imagery, never a search range."""
import hashlib
import json
from datetime import date
from pathlib import Path


def resolve_planting_cutoff(imagery, raster):
    if imagery.planting_date_cutoff is not None:
        return {"cutoff": imagery.planting_date_cutoff.isoformat(), "source": "explicit override"}
    raster = Path(raster)
    manifest = raster.with_suffix('.manifest.json')
    evidence = {}

    def read(path):
        raw = path.read_bytes()
        evidence[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    if not manifest.exists():
        if imagery.require_acquisition_date:
            raise ValueError(f"Missing acquisition manifest: {manifest}; supply an explicit planting_date_cutoff")
        return {"cutoff": None, "source": "acquisition metadata unavailable"}
    metadata = read(manifest)
    sources = metadata.get('sources') or [metadata]
    catalog_path = raster.parent.parent / 'stac-items.json'
    catalog = {item['id']: item for item in read(catalog_path)['items']} if catalog_path.exists() else {}
    dates = []
    for source in sources:
        item_id = source.get('item_id', raster.stem)
        acquired = source.get('acquisition_datetime')
        sidecar = raster.parent / f'{item_id}.manifest.json'
        if not acquired and sidecar.exists() and sidecar != manifest:
            acquired = read(sidecar).get('acquisition_datetime')
        if not acquired:
            acquired = catalog.get(item_id, {}).get('datetime')
        if not acquired:
            raise ValueError(f"Missing acquisition date for selected source {item_id}; cannot safely date mosaic")
        dates.append({"item_id": item_id, "date": date.fromisoformat(str(acquired)[:10]).isoformat()})
    return {"cutoff": max(item['date'] for item in dates), "source": "latest selected source acquisition day",
            "sources": dates, "metadata_sha256": evidence}
