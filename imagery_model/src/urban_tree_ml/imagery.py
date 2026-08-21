from __future__ import annotations

import json

import pandas as pd

from urban_tree_ml.config import ProjectConfig


def index_stac_coverage(config: ProjectConfig) -> dict[str, object]:
    """Record stable STAC item IDs; signed asset URLs are intentionally not persisted."""
    try:
        import pystac_client
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    imagery = config.imagery
    if not all((imagery.stac_url, imagery.collection, imagery.datetime, imagery.asset_key)):
        raise ValueError("STAC URL, collection, datetime, and asset key are required")

    inventory_path = (
        config.paths.root / "inventory" / config.inventory.city.lower() / "inventory.parquet"
    )
    frame = pd.read_parquet(inventory_path, columns=["longitude", "latitude"])
    bbox = [
        float(frame["longitude"].min()),
        float(frame["latitude"].min()),
        float(frame["longitude"].max()),
        float(frame["latitude"].max()),
    ]
    catalog = pystac_client.Client.open(imagery.stac_url)
    items = list(
        catalog.search(
            collections=[imagery.collection],
            bbox=bbox,
            datetime=imagery.datetime,
        ).item_collection()
    )
    items.sort(
        key=lambda item: ((item.datetime.isoformat() if item.datetime else ""), item.id),
        reverse=True,
    )

    records: list[dict[str, object]] = []
    for item in items:
        if imagery.asset_key not in item.assets:
            continue
        records.append(
            {
                "id": item.id,
                "collection": item.collection_id,
                "datetime": item.datetime.isoformat() if item.datetime else None,
                "bbox": item.bbox,
                "asset_key": imagery.asset_key,
                "asset_href": item.assets[imagery.asset_key].href,
                "properties": {
                    key: item.properties.get(key)
                    for key in ("gsd", "proj:epsg", "naip:year", "eo:cloud_cover")
                    if key in item.properties
                },
            }
        )

    output_dir = config.paths.root / "imagery" / config.inventory.city.lower()
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "stac-items.json"
    index_path.write_text(
        json.dumps(
            {
                "query": {
                    "stac_url": imagery.stac_url,
                    "collection": imagery.collection,
                    "datetime": imagery.datetime,
                    "bbox": bbox,
                },
                "items": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"items": len(records), "path": str(index_path), "bbox": bbox}
