"""Immutable prepared pixels in GCS, with a local read-through cache."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import time
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from urllib.parse import urlsplit
from uuid import uuid4

import numpy as np

FORMAT = "prepared-images-v1"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def source_stat(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def raster_identity(
    path: Path,
    memo: dict[Path, str] | None = None,
    states: dict[Path, tuple[int, int, int] | None] | None = None,
) -> str:
    """Hash source bytes, VRT dependencies and GDAL sidecars, independent of mount paths."""
    path = path.resolve(strict=True)
    memo = {} if memo is None else memo
    if path in memo:
        return memo[path]
    before = source_stat(path)
    if path.suffix.lower() == ".vrt":
        tree = ET.fromstring(path.read_bytes())
        for node in list(tree.iter("SourceFilename")) + list(tree.iter("SourceDataset")):
            dependency = Path(node.text or "")
            if not dependency.is_absolute():
                dependency = path.parent / dependency
            node.text = raster_identity(dependency, memo, states)
            node.attrib.pop("relativeToVRT", None)
        content_hash = digest(ET.tostring(tree))
    else:
        with path.open("rb") as stream:
            content_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    after = source_stat(path)
    if before != after:
        raise RuntimeError("Imagery changed while computing its cache identity")
    if states is not None:
        states[path] = after
    sidecars = {}
    for suffix in (".msk", ".aux.xml", ".ovr"):
        sidecar = Path(str(path) + suffix)
        if states is not None:
            states[sidecar] = source_stat(sidecar)
        if sidecar.is_file():
            sidecars[suffix] = raster_identity(sidecar, memo, states)
    identity = digest(canonical({"content": content_hash, "sidecars": sidecars}))
    memo[path] = identity
    return identity


class GCSStore:
    def __init__(self, uri: str):
        from google.cloud import storage

        parsed = urlsplit(uri)
        if parsed.scheme != "gs" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Prepared image cache must be a gs://bucket/prefix URI")
        self.bucket = storage.Client().bucket(parsed.netloc)
        self.prefix = parsed.path.strip("/")

    def blob(self, key: str):
        return self.bucket.blob("/".join(filter(None, (self.prefix, FORMAT, key + ".zip"))))

    def download(self, key: str, destination: Path) -> bool:
        from google.api_core.exceptions import NotFound

        try:
            self.blob(key).download_to_filename(str(destination), checksum="auto", timeout=60)
        except NotFound:
            return False
        return True

    def upload(self, key: str, source: Path) -> None:
        from google.api_core.exceptions import PreconditionFailed

        try:
            self.blob(key).upload_from_filename(
                str(source),
                content_type="application/zip",
                if_generation_match=0,
                checksum="auto",
                timeout=60,
            )
        except PreconditionFailed:
            # Another producer committed this immutable key first. Verify its
            # pixels before accepting it, even if ZIP container bytes differ.
            temporary = source.with_name(f".{uuid4().hex}.compare")
            try:
                if not self.download(key, temporary):
                    raise RuntimeError("Concurrent cache object disappeared")
                existing = PreparedImage.load(temporary, key)
                proposed = PreparedImage.load(source, key)
                if not (
                    np.array_equal(existing.image, proposed.image, equal_nan=True)
                    and np.array_equal(existing.valid_mask, proposed.valid_mask)
                ):
                    raise ValueError("Conflicting prepared pixels for the same cache key")
            finally:
                temporary.unlink(missing_ok=True)


@dataclass
class PreparedImage:
    image: np.ndarray
    valid_mask: np.ndarray
    archive: bytes

    @classmethod
    def load(cls, path: Path, key: str) -> PreparedImage:
        with zipfile.ZipFile(path) as archive:
            metadata = json.loads(archive.read("manifest.json"))
            image_bytes = archive.read("image.npz")
            mask_bytes = archive.read("valid-mask.npy")
        if metadata != {
            "key": key,
            "format": FORMAT,
            "image_sha256": digest(image_bytes),
            "mask_sha256": digest(mask_bytes),
        }:
            raise ValueError("Prepared image cache identity or checksum mismatch")
        with np.load(io.BytesIO(image_bytes), allow_pickle=False) as image_archive:
            if image_archive.files != ["image"]:
                raise ValueError("Unexpected arrays in prepared image cache")
            image = image_archive["image"]
        valid_mask = np.load(io.BytesIO(mask_bytes), allow_pickle=False)
        if (
            image.dtype != np.float32
            or image.ndim != 3
            or valid_mask.dtype != np.bool_
            or valid_mask.shape != image.shape[1:]
        ):
            raise ValueError("Invalid prepared image cache array schema")
        return cls(image, valid_mask, image_bytes)

    def write_targets(self, output: BinaryIO, targets: dict[str, np.ndarray]) -> None:
        """Reuse the compressed image member and compress only fresh supervision."""
        if "image" in targets:
            raise ValueError("Targets cannot replace cached imagery")
        output.write(self.archive)
        with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, array in targets.items():
                with archive.open(name + ".npy", "w", force_zip64=True) as member:
                    np.lib.format.write_array(member, np.asanyarray(array), allow_pickle=False)


class PreparedImageCache:
    def __init__(self, source, bands, scale: float, directory: Path, store, mask_recipe: str):
        import rasterio

        started = time.monotonic()
        self.source = source
        self.bands = list(bands)
        self.scale = scale
        self.directory = Path(directory) / FORMAT
        self.directory.mkdir(parents=True, exist_ok=True)
        self.store = store
        self.stats = dict(
            local_hits=0,
            remote_hits=0,
            misses=0,
            repaired_local_entries=0,
            downloaded_bytes=0,
            uploaded_bytes=0,
            remote_seconds=0.0,
        )
        # Decoder and mask implementation are part of the pixel recipe. Label,
        # split and loss settings deliberately do not enter the pixel identity.
        memo: dict[Path, str] = {}
        self.source_states = {}
        self.identity = dict(
            format=FORMAT,
            raster=raster_identity(Path(source.name), memo, self.source_states),
            dependencies=sorted(
                raster_identity(Path(p), memo, self.source_states)
                for p in source.files
                if Path(p).resolve() != Path(source.name).resolve()
            ),
            bands=self.bands,
            scale=scale,
            mask_recipe=mask_recipe,
            rasterio=rasterio.__version__,
            gdal=rasterio.__gdal_version__,
            numpy=np.__version__,
            platform=platform.system(),
            width=source.width,
            height=source.height,
            crs=str(source.crs),
            transform=list(source.transform),
        )
        self.validate_source()
        self.stats["identity_seconds"] = time.monotonic() - started

    @classmethod
    def configured(cls, source, imagery, root: Path, mask_recipe: str):
        uri = os.environ.get("TREE_ML_PREPARED_IMAGE_CACHE_URI")
        settings = Path(root) / "prepared-image-cache.json"
        if uri is None and settings.is_file():
            uri = json.loads(settings.read_text(encoding="utf-8"))["uri"]
        if not uri:
            return None
        directory = Path(
            os.environ.get(
                "TREE_ML_PREPARED_IMAGE_CACHE_DIR", str(Path(root) / "cache" / "prepared-images")
            )
        )
        return cls(
            source, imagery.bands, imagery.input_scale, directory, GCSStore(uri), mask_recipe
        )

    def validate_source(self) -> None:
        for path, expected in self.source_states.items():
            if source_stat(path) != expected:
                raise RuntimeError(f"Imagery changed during cache preparation: {path}")

    def read(self, window, mask_reader: Callable[[], np.ndarray]) -> PreparedImage:
        self.validate_source()
        key = digest(canonical({**self.identity, "window": list(window.flatten())}))
        target = self.directory / (key + ".zip")
        if target.exists():
            try:
                result = PreparedImage.load(target, key)
            except (ValueError, KeyError, EOFError, OSError, zipfile.BadZipFile):
                self.stats["repaired_local_entries"] += 1
            else:
                self.stats["local_hits"] += 1
                return result
        temporary = target.with_name(f".{key}.{uuid4().hex}.part")
        try:
            started = time.monotonic()
            found = self.store.download(key, temporary)
            self.stats["remote_seconds"] += time.monotonic() - started
            if found:
                result = PreparedImage.load(temporary, key)
                expected_shape = (len(self.bands), int(window.height), int(window.width))
                if result.image.shape != expected_shape:
                    raise ValueError("Cached image shape differs from requested window")
                self.stats["remote_hits"] += 1
                self.stats["downloaded_bytes"] += temporary.stat().st_size
            else:
                self.stats["misses"] += 1
                image = self.source.read(self.bands, window=window).astype(np.float32) * self.scale
                valid_mask = mask_reader()
                self.validate_source()
                image_buffer, mask_buffer = io.BytesIO(), io.BytesIO()
                np.savez_compressed(image_buffer, image=image)
                np.save(mask_buffer, valid_mask, allow_pickle=False)
                image_bytes, mask_bytes = image_buffer.getvalue(), mask_buffer.getvalue()
                metadata = dict(
                    key=key,
                    format=FORMAT,
                    image_sha256=digest(image_bytes),
                    mask_sha256=digest(mask_bytes),
                )
                with zipfile.ZipFile(temporary, "w") as archive:
                    archive.writestr("manifest.json", canonical(metadata))
                    archive.writestr("image.npz", image_bytes)
                    archive.writestr(
                        "valid-mask.npy", mask_bytes, compress_type=zipfile.ZIP_DEFLATED
                    )
                result = PreparedImage.load(temporary, key)
                started = time.monotonic()
                self.store.upload(key, temporary)
                self.stats["remote_seconds"] += time.monotonic() - started
                self.stats["uploaded_bytes"] += temporary.stat().st_size
            try:
                os.replace(temporary, target)
            except PermissionError:
                # Windows can deny replacement while a concurrent reader has
                # the winner open. Accept only a complete, equivalent entry.
                winner = PreparedImage.load(target, key)
                if not (
                    np.array_equal(winner.image, result.image, equal_nan=True)
                    and np.array_equal(winner.valid_mask, result.valid_mask)
                ):
                    raise ValueError("Conflicting local prepared image cache entry") from None
            return result
        finally:
            temporary.unlink(missing_ok=True)
