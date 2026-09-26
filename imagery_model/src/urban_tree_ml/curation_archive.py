"""Immutable curation revisions and off-machine backup; never finalizes live reviews."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from functools import lru_cache
from contextlib import nullcontext


def _write_json_atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@lru_cache(maxsize=8192)
def _versioned_digest(path, size, modified_ns, changed_ns):
    return digest(path)


def image_digest(path, verify=False):
    path = Path(path)
    if verify:
        return digest(path)
    stat = path.stat()
    return _versioned_digest(str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def archive_root(root):
    return Path(root) / "curation-archive"


def acquisition(root, raster, register=False):
    """Bind a rendered grid to immutable bytes, dates and provider assets, not basename alone."""
    import xml.etree.ElementTree as ET

    raster = Path(raster).resolve()
    directory = archive_root(root)
    key = hashlib.sha256(str(raster).encode()).hexdigest()
    registry = directory / "registrations" / f"{key}.json"
    if registry.exists() and not register:
        record = json.loads(registry.read_text())
        for item in record["assets"]:
            stat = Path(item["path"]).stat()
            if [stat.st_size, stat.st_mtime_ns] != item["stat"]:
                raise ValueError(
                    "Registered imagery changed; register a new acquisition before saving"
                )
        return record
    if not register:
        raise ValueError("Imagery not registered in curation archive")
    paths = [raster]
    if raster.suffix.lower() == ".vrt":
        tree = ET.parse(raster)
        for element in tree.findall(".//SourceFilename"):
            source = Path(element.text)
            if element.get("relativeToVRT") == "1":
                source = raster.parent / source
            paths.append(source.resolve())
    paths = sorted(set(paths))
    for path in list(paths):
        sidecar = path.with_suffix(".manifest.json")
        if sidecar.exists():
            paths.append(sidecar)
    assets, provenance = [], []
    for path in paths:
        print(f"Hashing acquisition asset: {path.name}", flush=True)
        stat = path.stat()
        sha = digest(path)
        if [path.stat().st_size, path.stat().st_mtime_ns] != [stat.st_size, stat.st_mtime_ns]:
            raise ValueError("Imagery changed while hashing")
        assets.append(
            {
                "path": str(path),
                "name": path.name,
                "sha256": sha,
                "stat": [stat.st_size, stat.st_mtime_ns],
            }
        )
        if path.name.endswith(".manifest.json"):
            data = json.loads(path.read_text())
            provenance.append(
                {
                    k: data.get(k)
                    for k in [
                        "collection",
                        "item_id",
                        "acquisition_datetime",
                        "asset_key",
                        "raster",
                    ]
                }
            )
    identity = {
        "schema_version": 1,
        "provider": "planetary_computer",
        "collection": "naip",
        "assets": sorted(
            ({"name": a["name"], "sha256": a["sha256"]} for a in assets), key=lambda a: a["name"]
        ),
        "provenance": provenance,
    }
    acquisition_id = hashlib.sha256(canonical(identity)).hexdigest()
    record = {**identity, "acquisition_id": acquisition_id, "assets": assets}
    target = directory / "acquisitions" / f"{acquisition_id}.json"
    if not target.exists():
        _write_json_atomic(target, record)
    _write_json_atomic(registry, record)
    return record


def capture(root, bundle_dir, review_dir, raster, verify_images=False):
    """Copy and validate a complete bundle; fail/retry rather than mix concurrent autosaves."""
    bundle_dir, review_dir = Path(bundle_dir), Path(review_dir)
    first = (bundle_dir / "bundle.json").read_bytes()
    bundle = json.loads(first)
    files = {"bundle.json": first}
    for name, expected in bundle["files"].items():
        if name not in {"reviews.json", "manifest.json", "training-feedback.json"}:
            raise ValueError("Unexpected bundle filename")
        content = (bundle_dir / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != expected["sha256"]:
            raise ValueError("Bundle is changing; retry snapshot")
        files[name] = content
    if first != (bundle_dir / "bundle.json").read_bytes():
        raise ValueError("Bundle changed; retry snapshot")
    source = acquisition(root, raster)
    directory = archive_root(root)
    binding_path = directory / "bindings" / f"{bundle['city'].lower()}-{bundle['review_id']}.json"
    binding = {"acquisition_id": source["acquisition_id"]}
    if binding_path.exists():
        if json.loads(binding_path.read_text()) != binding:
            raise ValueError("Existing review belongs to different imagery; create a new review ID")
    else:
        _write_json_atomic(binding_path, binding)
    images = {}
    manifest = json.loads(files["manifest.json"])
    for scene in manifest["scenes"]:
        relative = scene["image"]
        path = (review_dir / relative).resolve()
        if not path.is_relative_to(review_dir.resolve()):
            raise ValueError("Review image escapes its directory")
        sha = image_digest(path, verify_images)
        destination = directory / "images" / sha
        if not destination.exists():
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() != sha:
                raise ValueError('Review image changed during snapshot; retry')
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive immutable object: one producer under the server's save lock.
            temporary_image = destination.with_name("." + uuid4().hex + ".tmp")
            with temporary_image.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_image, destination)
        elif image_digest(destination, verify_images) != sha:
            raise ValueError("Corrupt archived image")
        images[relative] = sha
    identity = {
        "city": bundle["city"].lower(),
        "review_id": bundle["review_id"],
        "acquisition_id": source["acquisition_id"],
        "images": images,
        "manifest_sha256": bundle["source_manifest_sha256"],
        "review_state_sha256": bundle["review_state_sha256"],
        "feedback_current": bundle["feedback_current"],
    }
    revision = hashlib.sha256(canonical(identity)).hexdigest()
    identity["revision"] = revision
    files["archive.json"] = canonical(identity)
    target = directory / "revisions" / f"{revision}.zip"
    if target.exists():
        try:
            with zipfile.ZipFile(target) as archive:
                if json.loads(archive.read("archive.json")) != identity:
                    raise ValueError("Corrupt archived revision identity")
                saved_bundle = json.loads(archive.read("bundle.json"))
                for name, expected in saved_bundle["files"].items():
                    if hashlib.sha256(archive.read(name)).hexdigest() != expected["sha256"]:
                        raise ValueError("Corrupt archived revision content")
        except zipfile.BadZipFile as error:
            raise ValueError("Corrupt archived revision ZIP") from error
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        temporary = target.with_name("." + uuid4().hex + ".tmp")
        with temporary.open("wb") as stream:
            stream.write(buffer.getvalue())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    _write_json_atomic(directory / "latest" / f"{identity['city']}.json", identity)
    return identity


def command(*args):
    return subprocess.check_output(list(map(str, args)), text=True).strip()


def upload_file(path, url):
    # Never delete/replace an immutable object. gcloud validates transfer checksums.
    command(
        "gcloud.cmd" if os.name == "nt" else "gcloud", "storage", "cp", "--no-clobber", path, url
    )


def backup(root, annotations, bucket, capture_lock=None):
    root, annotations = Path(root), Path(annotations)
    directory = archive_root(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "backup.lock"
    with lock.open("x") as handle:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()}, handle)
    try:
        snapshots = []
        for city, review, year in [
            ("ussfo", "ussfo-2022-mosaic", "2022"),
            ("usbos", "usbos-2023-external", "2023"),
        ]:
            review_dir = root / "qa" / "registration" / review
            manifest = json.loads((review_dir / "manifest.json").read_text())
            bundle_dir = annotations / city / manifest["metadata"]["review_id"]
            raster = root / "imagery" / city / year / f"{review}.vrt"
            with capture_lock or nullcontext():
                snapshots.append(capture(root, bundle_dir, review_dir, raster, verify_images=True))
        # Publish stable copies through an isolated clone, never git-add the live autosave files.
        publisher = directory / "publisher"
        if not (publisher / ".git").exists():
            command(
                "git", "clone", "https://github.com/arborary-world/training-data.git", publisher
            )
        command("git", "-C", publisher, "config", "core.autocrlf", "false")
        command("git", "-C", publisher, "pull", "--ff-only")
        attributes = publisher / ".gitattributes"
        existing = attributes.read_text() if attributes.exists() else ""
        if "annotations/** -text" not in existing:
            attributes.write_text(
                existing
                + "\n# Preserve checksummed annotation bytes on every OS.\nannotations/** -text\n"
            )
        for snapshot in snapshots:
            target = publisher / "annotations" / snapshot["city"] / snapshot["review_id"]
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(
                directory / "revisions" / f"{snapshot['revision']}.zip"
            ) as archive:
                for name in [
                    "bundle.json",
                    "manifest.json",
                    "reviews.json",
                    "training-feedback.json",
                ]:
                    if name in archive.namelist():
                        (target / name).write_bytes(archive.read(name))
                    elif name == "training-feedback.json":
                        (target / name).unlink(missing_ok=True)
        runner = publisher / "tools" / "curation_archive.py"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_bytes(Path(__file__).read_bytes())
        command(
            "git",
            "-C",
            publisher,
            "add",
            "--",
            "annotations",
            ".gitattributes",
            "tools/curation_archive.py",
        )
        if command("git", "-C", publisher, "diff", "--cached", "--name-only"):
            command(
                "git",
                "-C",
                publisher,
                "commit",
                "-m",
                "Back up current in-progress curation snapshots",
            )
        command("git", "-C", publisher, "push", "origin", "HEAD:main")
        commit = command("git", "-C", publisher, "rev-parse", "HEAD")
        for snapshot in snapshots:
            with zipfile.ZipFile(
                directory / "revisions" / f"{snapshot['revision']}.zip"
            ) as archive:
                bundle = json.loads(archive.read("bundle.json"))
                for name, expected in bundle["files"].items():
                    git_path = f"annotations/{snapshot['city']}/{snapshot['review_id']}/{name}"
                    content = subprocess.check_output(
                        ["git", "-C", str(publisher), "show", f"{commit}:{git_path}"]
                    )
                    if hashlib.sha256(content).hexdigest() != expected["sha256"]:
                        raise ValueError("Committed annotation checksum mismatch")
        for snapshot in snapshots:
            _write_json_atomic(
                directory / "git-status" / f"{snapshot['city']}.json",
                {**snapshot, "commit": commit, "at": datetime.now(UTC).isoformat()},
            )
        # Upload all immutable history, including revisions between scheduled backups.
        receipt_path = directory / "uploaded.json"
        receipts = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        objects = []
        for path in (directory / "acquisitions").glob("*.json"):
            record = json.loads(path.read_text())
            for asset in record["assets"]:
                objects.append((Path(asset["path"]), "objects/" + asset["sha256"]))
            objects.append((path, "acquisitions/" + path.name))
        for path, key in objects:
            if key in receipts:
                continue
            sha = digest(path)
            if key.startswith("objects/") and key.split("/")[-1] != sha:
                raise ValueError("Source asset changed before backup")
            upload_file(path, bucket.rstrip("/") + "/" + key)
            receipts[key] = {"sha256": sha, "at": datetime.now(UTC).isoformat()}
            _write_json_atomic(receipt_path, receipts)
        for category in ["images", "revisions", "bindings"]:
            command(
                "gcloud.cmd" if os.name == "nt" else "gcloud",
                "storage",
                "rsync",
                directory / category,
                bucket.rstrip("/") + "/" + category,
                "--recursive",
                "--exclude=.*\\.tmp$",
            )
        for snapshot in snapshots:
            receipt = directory / "remote-status" / f"{snapshot['city']}.json"
            _write_json_atomic(
                receipt,
                {**snapshot, "bucket": bucket, "at": datetime.now(UTC).isoformat()},
            )
            gc = "gcloud.cmd" if os.name == "nt" else "gcloud"
            command(
                gc,
                "storage",
                "cp",
                receipt,
                f"{bucket}/catalogs/{snapshot['city']}/{snapshot['revision']}.json",
            )
            command(gc, "storage", "cp", receipt, f"{bucket}/latest/{snapshot['city']}.json")
        print("Annotation Git push and private object backup completed", flush=True)
    finally:
        lock.unlink(missing_ok=True)


def status(root, city, state_revision, feedback_current, manifest_revision=None):
    directory = archive_root(root)
    result = {"local_revision": state_revision, "published_for_training": feedback_current}
    error = directory / "errors" / f"{city}.json"
    result["archive_error"] = json.loads(error.read_text()).get("error") if error.exists() else None
    for name, subdir in [("remote", "remote-status"), ("git", "git-status")]:
        path = directory / subdir / f"{city}.json"
        saved = json.loads(path.read_text()) if path.exists() else {}
        result[name] = {
            **saved,
            "current": (
                saved.get("review_state_sha256") == state_revision
                and (manifest_revision is None or saved.get("manifest_sha256") == manifest_revision)
            ),
        }
    return result


def restore(root, revision, destination, bucket, include_sources=False):
    """Restore to a NEW directory; never touch live review state. Verify every fetched object."""
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid revision ID")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    gc = "gcloud.cmd" if os.name == "nt" else "gcloud"
    packet = destination / "revision.zip"
    command(gc, "storage", "cp", f"{bucket}/revisions/{revision}.zip", packet)
    with zipfile.ZipFile(packet) as archive:
        info = json.loads(archive.read("archive.json"))
        identity = {k: v for k, v in info.items() if k != "revision"}
        if (
            info["revision"] != revision
            or hashlib.sha256(canonical(identity)).hexdigest() != revision
        ):
            raise ValueError("Revision identity mismatch")
        bundle = json.loads(archive.read("bundle.json"))
        for name, expected in bundle["files"].items():
            if name not in {"manifest.json", "reviews.json", "training-feedback.json"}:
                raise ValueError("Unexpected revision file")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected["sha256"]:
                raise ValueError("Restored annotation checksum mismatch")
            (destination / name).write_bytes(content)
        for name in ["archive.json", "bundle.json"]:
            (destination / name).write_bytes(archive.read(name))
    # Validate the stored state/manifest against the revision, not merely internal ZIP checksums.
    reviews = json.loads((destination / "reviews.json").read_text())
    manifest = json.loads((destination / "manifest.json").read_text())
    from urban_tree_ml.feedback import load_persisted_reviews
    restored = load_persisted_reviews(destination, migrate_crowns=reviews.get('crown_storage_version') == 1) if reviews.get('schema_version') == 2 else reviews
    state = {"reviews": restored["reviews"], "scene_reviews": restored["scene_reviews"]}
    if restored.get("mask_regions"):
        state["mask_regions"] = restored["mask_regions"]
    if (
        hashlib.sha256(canonical(manifest)).hexdigest() != info["manifest_sha256"]
        or hashlib.sha256(canonical(state)).hexdigest() != info["review_state_sha256"]
    ):
        raise ValueError("Restored state differs from revision")
    image_objects = destination / ".image-objects"
    image_objects.mkdir()
    command(gc, "storage", "rsync", f"{bucket}/images", image_objects, "--recursive")
    for relative, sha in info["images"].items():
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination.resolve()):
            raise ValueError("Unsafe image path")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((image_objects / sha).read_bytes())
        if digest(target) != sha:
            raise ValueError("Restored image checksum mismatch")
    if include_sources:
        identity_path = destination / "acquisition.json"
        command(
            gc,
            "storage",
            "cp",
            f"{bucket}/acquisitions/{info['acquisition_id']}.json",
            identity_path,
        )
        acquisition_record = json.loads(identity_path.read_text())
        acquisition_identity = {
            k: acquisition_record[k]
            for k in ["schema_version", "provider", "collection", "provenance"]
        }
        acquisition_identity["assets"] = sorted(
            ({"name": a["name"], "sha256": a["sha256"]} for a in acquisition_record["assets"]),
            key=lambda a: a["name"],
        )
        if hashlib.sha256(canonical(acquisition_identity)).hexdigest() != info["acquisition_id"]:
            raise ValueError("Acquisition identity checksum mismatch")
        source_dir = destination / "source-imagery"
        source_dir.mkdir()
        for item in acquisition_record["assets"]:
            if Path(item["name"]).name != item["name"]:
                raise ValueError("Unsafe asset name")
            target = source_dir / item["name"]
            command(gc, "storage", "cp", f"{bucket}/objects/{item['sha256']}", target)
            if digest(target) != item["sha256"]:
                raise ValueError("Restored source checksum mismatch")
    _write_json_atomic(
        destination / "RESTORE_VERIFIED.json",
        {
            "revision": revision,
            "images": len(info["images"]),
            "source_imagery": include_sources,
            "at": datetime.now(UTC).isoformat(),
        },
    )
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--bucket", default="gs://arborary-world-curation-archive")
    parser.add_argument("--register", type=Path)
    parser.add_argument("--restore")
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--include-sources", action="store_true")
    args = parser.parse_args()
    if args.restore:
        if not args.destination:
            parser.error("--destination required")
        restore(args.root, args.restore, args.destination, args.bucket, args.include_sources)
    elif args.register:
        print(acquisition(args.root, args.register, register=True)["acquisition_id"])
    elif args.annotations:
        backup(args.root, args.annotations, args.bucket)
    else:
        parser.error("--annotations or --register required")
