from __future__ import annotations

import hashlib
import json
import os
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

import pandas as pd

from urban_tree_ml.config import ProjectConfig

_EXPECTED_NAIP_BANDS = ("red", "green", "blue", "nir")
_GDAL_DATA_TYPES = {
    "uint8": "Byte",
    "uint16": "UInt16",
    "int16": "Int16",
    "uint32": "UInt32",
    "int32": "Int32",
    "float32": "Float32",
    "float64": "Float64",
}


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
        item_bbox = item.bbox
        inventory_tree_count = 0
        if item_bbox and len(item_bbox) >= 4:
            inventory_tree_count = int(
                (
                    frame["longitude"].between(float(item_bbox[0]), float(item_bbox[2]))
                    & frame["latitude"].between(float(item_bbox[1]), float(item_bbox[3]))
                ).sum()
            )
        records.append(
            {
                "id": item.id,
                "collection": item.collection_id,
                "datetime": item.datetime.isoformat() if item.datetime else None,
                "bbox": item.bbox,
                "inventory_tree_count": inventory_tree_count,
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
    items_by_year: dict[str, int] = {}
    for record in records:
        year = str(record.get("properties", {}).get("naip:year") or "unknown")
        items_by_year[year] = items_by_year.get(year, 0) + 1
    ranked = sorted(
        records,
        key=lambda record: (-int(record["inventory_tree_count"]), str(record["id"])),
    )
    return {
        "items": len(records),
        "items_by_year": items_by_year,
        "top_items_by_inventory_count": [
            {
                "id": record["id"],
                "year": record.get("properties", {}).get("naip:year"),
                "inventory_tree_count": record["inventory_tree_count"],
            }
            for record in ranked[:12]
        ],
        "path": str(index_path),
        "bbox": bbox,
    }


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


def fetch_configured_stac_items(
    config: ProjectConfig,
    *,
    overwrite: bool = False,
) -> dict[str, object]:
    """Fetch the explicit, reproducible imagery footprint declared by a city config."""
    if not config.imagery.item_ids:
        raise ValueError("imagery.item_ids is empty; select indexed STAC items first")
    results = [
        fetch_stac_item(config, item_id, overwrite=overwrite)
        for item_id in config.imagery.item_ids
    ]
    return {
        "city": config.inventory.city,
        "items": len(results),
        "downloaded": sum(result["status"] == "downloaded" for result in results),
        "existing": sum(result["status"] == "existing" for result in results),
        "results": results,
    }


def build_vrt_mosaic(
    config: ProjectConfig,
    year: str,
    *,
    output: str | Path | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Build a lightweight, validated VRT over the downloaded tiles for one year."""
    if len(year) != 4 or not year.isdigit():
        raise ValueError("year must contain exactly four digits")
    try:
        import rasterio
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    city = config.inventory.city.lower()
    imagery_dir = config.paths.root / "imagery" / city / year
    downloaded = {path.stem: path for path in imagery_dir.glob("*.tif")}
    if config.imagery.item_ids:
        missing = [item_id for item_id in config.imagery.item_ids if item_id not in downloaded]
        if missing:
            raise FileNotFoundError(
                "configured imagery items have not been downloaded: " + ", ".join(missing)
            )
        sources = [downloaded[item_id] for item_id in config.imagery.item_ids]
    else:
        sources = sorted(downloaded.values())
    if not sources:
        raise FileNotFoundError(f"no downloaded GeoTIFFs found under {imagery_dir}")

    destination = (
        Path(output)
        if output is not None
        else imagery_dir / f"{city}-{year}-mosaic.vrt"
    )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_suffix(".manifest.json")
    if destination.exists() and not overwrite:
        with rasterio.open(destination) as mosaic:
            raster = {
                "width": mosaic.width,
                "height": mosaic.height,
                "crs": mosaic.crs.to_string() if mosaic.crs else None,
                "resolution": [abs(float(value)) for value in mosaic.res],
                "raster_band_count": mosaic.count,
                "selected_bands": config.imagery.bands,
            }
        return {
            "status": "existing",
            "path": str(destination),
            "manifest": str(manifest_path) if manifest_path.exists() else None,
            "sources": len(sources),
            "raster": raster,
        }

    source_records: list[dict[str, object]] = []
    reference_crs = None
    reference_count = 0
    reference_dtypes: tuple[str, ...] = ()
    x_resolution = 0.0
    y_resolution = 0.0
    left = float("inf")
    top = float("-inf")
    for source_path in sources:
        with rasterio.open(source_path) as source:
            if source.crs is None:
                raise ValueError(f"source raster has no CRS: {source_path}")
            if source.transform.b != 0 or source.transform.d != 0:
                raise ValueError(f"rotated source rasters are not supported: {source_path}")
            source_x_resolution, source_y_resolution = (
                abs(float(value)) for value in source.res
            )
            if not source_records:
                reference_crs = source.crs
                reference_count = source.count
                reference_dtypes = tuple(source.dtypes)
                x_resolution = source_x_resolution
                y_resolution = source_y_resolution
            elif (
                source.crs != reference_crs
                or source.count != reference_count
                or tuple(source.dtypes) != reference_dtypes
                or abs(source_x_resolution - x_resolution) > 1e-9
                or abs(source_y_resolution - y_resolution) > 1e-9
            ):
                raise ValueError("mosaic sources must share CRS, band count, dtype, and resolution")
            left = min(left, float(source.bounds.left))
            top = max(top, float(source.bounds.top))
            source_records.append(
                {
                    "path": source_path.resolve(),
                    "width": source.width,
                    "height": source.height,
                    "left": float(source.bounds.left),
                    "top": float(source.bounds.top),
                    "color_interpretations": [value.name for value in source.colorinterp],
                }
            )

    for record in source_records:
        column_offset = (float(record["left"]) - left) / x_resolution
        row_offset = (top - float(record["top"])) / y_resolution
        rounded_column = round(column_offset)
        rounded_row = round(row_offset)
        if abs(column_offset - rounded_column) > 1e-6 or abs(row_offset - rounded_row) > 1e-6:
            raise ValueError("mosaic sources are not aligned to a common pixel grid")
        record["column_offset"] = rounded_column
        record["row_offset"] = rounded_row

    width = max(int(record["column_offset"]) + int(record["width"]) for record in source_records)
    height = max(int(record["row_offset"]) + int(record["height"]) for record in source_records)
    vrt = ET.Element("VRTDataset", rasterXSize=str(width), rasterYSize=str(height))
    ET.SubElement(vrt, "SRS").text = reference_crs.to_wkt()  # type: ignore[union-attr]
    ET.SubElement(vrt, "GeoTransform").text = (
        f"{left:.15g}, {x_resolution:.15g}, 0, {top:.15g}, 0, {-y_resolution:.15g}"
    )
    for band_index, dtype in enumerate(reference_dtypes, start=1):
        try:
            data_type = _GDAL_DATA_TYPES[dtype]
        except KeyError:
            raise ValueError(f"unsupported VRT source dtype: {dtype}") from None
        band = ET.SubElement(
            vrt,
            "VRTRasterBand",
            dataType=data_type,
            band=str(band_index),
        )
        color_interpretation = str(source_records[0]["color_interpretations"][band_index - 1])
        if color_interpretation != "undefined":
            ET.SubElement(band, "ColorInterp").text = color_interpretation.title()
        for record in source_records:
            simple_source = ET.SubElement(band, "SimpleSource")
            relative_path = os.path.relpath(record["path"], destination.parent).replace(os.sep, "/")
            ET.SubElement(
                simple_source,
                "SourceFilename",
                relativeToVRT="1",
            ).text = relative_path
            ET.SubElement(simple_source, "SourceBand").text = str(band_index)
            ET.SubElement(
                simple_source,
                "SrcRect",
                xOff="0",
                yOff="0",
                xSize=str(record["width"]),
                ySize=str(record["height"]),
            )
            ET.SubElement(
                simple_source,
                "DstRect",
                xOff=str(record["column_offset"]),
                yOff=str(record["row_offset"]),
                xSize=str(record["width"]),
                ySize=str(record["height"]),
            )

    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        ET.indent(vrt)
        ET.ElementTree(vrt).write(temporary, encoding="utf-8", xml_declaration=True)
        raster = _validate_raster(
            temporary,
            config,
            {
                "collection": "naip",
                "asset_key": "image",
                "band_names": list(_EXPECTED_NAIP_BANDS),
            },
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    provenance: list[dict[str, object]] = []
    for record in source_records:
        source_path = Path(record["path"])
        source_manifest_path = source_path.with_suffix(".manifest.json")
        source_manifest = (
            json.loads(source_manifest_path.read_text(encoding="utf-8"))
            if source_manifest_path.exists()
            else {}
        )
        provenance.append(
            {
                "item_id": source_manifest.get("item_id", source_path.stem),
                "path": str(source_path),
                "manifest": str(source_manifest_path) if source_manifest_path.exists() else None,
                "bytes": source_path.stat().st_size,
                "sha256": source_manifest.get("sha256"),
                "bounds": [
                    float(record["left"]),
                    float(record["top"]) - int(record["height"]) * y_resolution,
                    float(record["left"]) + int(record["width"]) * x_resolution,
                    float(record["top"]),
                ],
            }
        )
    manifest = {
        "city": config.inventory.city,
        "year": year,
        "created_at": datetime.now(UTC).isoformat(),
        "path": str(destination),
        "sources": provenance,
        "raster": raster,
    }
    _write_json_atomic(manifest_path, manifest)
    return {
        "status": "created",
        "path": str(destination),
        "manifest": str(manifest_path),
        "sources": len(source_records),
        "raster": raster,
    }
