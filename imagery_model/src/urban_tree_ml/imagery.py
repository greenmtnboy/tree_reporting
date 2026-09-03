from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import pandas as pd

from urban_tree_ml.config import ProjectConfig

_EXPECTED_NAIP_BANDS = ("red", "green", "blue", "nir")


def _asset_band_names(asset: object) -> list[str]:
    extra_fields = getattr(asset, "extra_fields", {})
    bands = extra_fields.get("eo:bands") or extra_fields.get("raster:bands") or []
    names: list[str] = []
    for band in bands:
        if not isinstance(band, dict):
            continue
        name = band.get("common_name") or band.get("name")
        if name:
            names.append(str(name).lower())
    return names


def _sign_asset_href(href: str) -> str:
    try:
        import planetary_computer
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error
    return str(planetary_computer.sign(href))


def _stream_download(url: str, destination: Path) -> tuple[int, int | None, str]:
    request = Request(url, headers={"User-Agent": "urban-tree-ml/0.1"})
    digest = hashlib.sha256()
    downloaded = 0
    expected_bytes: int | None = None
    try:
        with urlopen(request, timeout=120) as response, destination.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            expected_bytes = int(content_length) if content_length is not None else None
            while chunk := response.read(8 * 1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                downloaded += len(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception as error:
        # Signed Planetary Computer URLs contain short-lived credentials. Do not
        # include the URL (which urllib exceptions may retain) in our error.
        raise RuntimeError(f"imagery download failed: {type(error).__name__}") from None

    if downloaded == 0:
        raise RuntimeError("imagery download returned an empty response")
    if expected_bytes is not None and downloaded != expected_bytes:
        raise RuntimeError(
            f"imagery download was truncated: expected {expected_bytes} bytes, got {downloaded}"
        )
    return downloaded, expected_bytes, digest.hexdigest()


def _validate_raster(
    path: Path,
    config: ProjectConfig,
    record: dict[str, object],
) -> dict[str, object]:
    try:
        import rasterio
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    with rasterio.open(path) as source:
        if source.crs is None:
            raise ValueError("downloaded imagery does not declare a CRS")
        if not source.crs.is_projected:
            raise ValueError(f"downloaded imagery CRS must be projected, got {source.crs}")
        if max(config.imagery.bands) > source.count:
            raise ValueError(
                f"configured band {max(config.imagery.bands)} exceeds raster count {source.count}"
            )

        x_resolution, y_resolution = (abs(float(value)) for value in source.res)
        expected_resolution = config.imagery.resolution_m
        resolution_tolerance = max(0.01, expected_resolution * 0.02)
        if (
            abs(x_resolution - expected_resolution) > resolution_tolerance
            or abs(y_resolution - expected_resolution) > resolution_tolerance
        ):
            raise ValueError(
                "downloaded imagery resolution does not match the config: "
                f"expected {expected_resolution} m, got {x_resolution} x {y_resolution}"
            )

        selected_dtypes = [source.dtypes[index - 1] for index in config.imagery.bands]
        if record.get("collection") == "naip" and any(
            dtype != "uint8" for dtype in selected_dtypes
        ):
            raise ValueError(f"NAIP image bands must be uint8, got {selected_dtypes}")

        stac_band_names = [str(name).lower() for name in record.get("band_names", [])]
        selected_band_names = [
            stac_band_names[index - 1]
            for index in config.imagery.bands
            if index <= len(stac_band_names)
        ]
        color_interpretations = [
            source.colorinterp[index - 1].name for index in config.imagery.bands
        ]
        if stac_band_names:
            if selected_band_names != list(_EXPECTED_NAIP_BANDS):
                raise ValueError(
                    "STAC band order does not match red, green, blue, NIR: "
                    f"got {selected_band_names}"
                )
            band_order_evidence = "stac-band-metadata"
        elif record.get("collection") == "naip" and record.get("asset_key") == "image":
            # The Planetary Computer NAIP `image` asset contract is RGB-NIR. The
            # TIFF itself generally labels RGB but leaves its fourth band undefined.
            if color_interpretations[:3] not in (
                ["red", "green", "blue"],
                ["gray", "undefined", "undefined"],
            ):
                raise ValueError(
                    "raster color interpretation contradicts NAIP RGB-NIR order: "
                    f"got {color_interpretations}"
                )
            if color_interpretations[3] == "alpha":
                raise ValueError("NAIP band 4 was marked alpha instead of near infrared")
            selected_band_names = list(_EXPECTED_NAIP_BANDS)
            band_order_evidence = "planetary-computer-naip-image-contract"
        else:
            raise ValueError("unable to verify the configured raster band order")

        return {
            "driver": source.driver,
            "width": source.width,
            "height": source.height,
            "crs": source.crs.to_string(),
            "resolution": [x_resolution, y_resolution],
            "raster_band_count": source.count,
            "selected_bands": config.imagery.bands,
            "selected_band_names": selected_band_names,
            "selected_dtypes": selected_dtypes,
            "color_interpretations": color_interpretations,
            "band_order_evidence": band_order_evidence,
        }


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


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
        asset = item.assets[imagery.asset_key]
        records.append(
            {
                "id": item.id,
                "collection": item.collection_id,
                "datetime": item.datetime.isoformat() if item.datetime else None,
                "bbox": item.bbox,
                "asset_key": imagery.asset_key,
                "asset_href": asset.href,
                "asset_media_type": asset.media_type,
                "band_names": _asset_band_names(asset),
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


def fetch_stac_item(
    config: ProjectConfig,
    item_id: str,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Download one indexed STAC asset without persisting its signed URL."""
    if not item_id or Path(item_id).name != item_id or "/" in item_id or "\\" in item_id:
        raise ValueError("item ID must be a non-empty filename-safe STAC identifier")

    city = config.inventory.city.lower()
    index_path = config.paths.root / "imagery" / city / "stac-items.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"imagery index does not exist at {index_path}; run imagery index first"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    record = next((item for item in index.get("items", []) if item.get("id") == item_id), None)
    if record is None:
        raise ValueError(f"STAC item {item_id!r} is not present in {index_path}")

    acquisition_datetime = str(record.get("datetime") or "")
    year = acquisition_datetime[:4]
    if not year.isdigit():
        year = str(record.get("properties", {}).get("naip:year") or "unknown")
    output_dir = config.paths.root / "imagery" / city / year
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{item_id}.tif"
    manifest_path = output_dir / f"{item_id}.manifest.json"

    if destination.exists() and not overwrite:
        raster = _validate_raster(destination, config, record)
        existing_manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.exists()
            else {}
        )
        return {
            "status": "existing",
            "item_id": item_id,
            "path": str(destination),
            "manifest": str(manifest_path) if manifest_path.exists() else None,
            "bytes": destination.stat().st_size,
            "sha256": existing_manifest.get("sha256"),
            "raster": raster,
        }

    unsigned_href = str(record["asset_href"])
    signed_href = _sign_asset_href(unsigned_href)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.part")
    try:
        downloaded, expected_bytes, sha256 = _stream_download(signed_href, temporary)
        raster = _validate_raster(temporary, config, record)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    manifest = {
        "item_id": item_id,
        "collection": record.get("collection"),
        "asset_key": record.get("asset_key"),
        "source_href": unsigned_href,
        "acquisition_datetime": record.get("datetime"),
        "downloaded_at": datetime.now(UTC).isoformat(),
        "bytes": downloaded,
        "server_content_length": expected_bytes,
        "sha256": sha256,
        "raster": raster,
    }
    _write_json_atomic(manifest_path, manifest)
    return {
        "status": "downloaded",
        "item_id": item_id,
        "path": str(destination),
        "manifest": str(manifest_path),
        "bytes": downloaded,
        "sha256": sha256,
        "raster": raster,
    }
