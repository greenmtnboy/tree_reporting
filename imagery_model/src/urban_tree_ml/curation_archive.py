"""Immutable curation revisions and off-machine backup; never finalizes live reviews."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import shutil
import tempfile
import time
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from functools import lru_cache
from contextlib import nullcontext


def _replace_with_retry(source, destination):
    """Windows readers can briefly deny rename; keep the prior file intact."""
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if os.name != 'nt' or attempt == 7:
                raise
            time.sleep(min(.025 * 2 ** attempt, .4))


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
        _replace_with_retry(temporary, path)
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


def workspace_status_key(city, review_id=None):
    """Different acquisitions in one city have independent backup state."""
    return city.lower() + ('-' + hashlib.sha256(review_id.encode()).hexdigest() if review_id else '')


def _chunks(content):
    """Content-defined JSON/line boundaries; exact bytes, no reserialization.

    Boundaries depend on nearby content, not absolute offsets, so edits don't
    invalidate every subsequent chunk. Long records have a 256 KiB safety cap.
    """
    start = 0
    for match in re.finditer(rb'\n|(?<=[},])(?=[{"\[])', content):
        end = match.end()
        while end - start > 262144:
            yield content[start:start + 262144]
            start += 262144
        if end - start >= 32768 and hashlib.sha256(content[max(start, end-64):end]).digest()[0] & 127 == 0:
            yield content[start:end]
            start = end
    while start < len(content):
        end = min(start + 262144, len(content))
        yield content[start:end]
        start = end


def _valid_sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _store_revision(directory, revision, files):
    """Independent manifests share immutable compressed chunks; no delta chains."""
    directory = Path(directory)
    target = directory / 'revisions-v2' / (revision + '.json')
    if target.exists():
        saved = _read_revision(directory, revision)
        # Snapshot regeneration changes only bundle.created_at even when the
        # revision's annotation state is identical. Keep the original packet.
        def stable(values):
            result = dict(values)
            if 'bundle.json' in result:
                bundle = json.loads(result['bundle.json'])
                bundle.pop('created_at', None)
                result['bundle.json'] = canonical(bundle)
            return result
        if stable(saved) != stable(files):
            raise ValueError('Immutable revision descriptor differs')
        return
    folder = directory / 'chunks'
    folder.mkdir(parents=True, exist_ok=True)
    entries = {}
    for name, content in files.items():
        file_sha = hashlib.sha256(content).hexdigest()
        reference = directory / 'file-references' / (file_sha + '.json')
        if reference.exists():
            entry = json.loads(reference.read_text())
            if entry['sha256'] != file_sha or entry['size'] != len(content):
                raise ValueError('File reference differs from source')
            entries[name] = entry
            continue
        parts = []
        for part in _chunks(content):
            sha = hashlib.sha256(part).hexdigest()
            path = folder / (sha + '.zlib')
            if path.exists():
                if hashlib.sha256(zlib.decompress(path.read_bytes())).hexdigest() != sha:
                    raise ValueError('Corrupt existing annotation chunk')
            else:
                temporary = folder / ('.' + uuid4().hex + '.tmp')
                try:
                    with temporary.open('xb') as stream:
                        stream.write(zlib.compress(part))
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            parts.append(sha)
        index = {'sha256': file_sha, 'size': len(content), 'chunks': parts}
        index_sha = hashlib.sha256(canonical(index)).hexdigest()
        _write_json_atomic(directory/'file-index'/(index_sha+'.json'),index)
        entries[name] = {'sha256': file_sha, 'size': len(content), 'index_sha256': index_sha}
        _write_json_atomic(reference,entries[name])
    descriptor = {'schema_version': 2, 'revision': revision, 'files': entries}
    if target.exists() and json.loads(target.read_text()) != descriptor:
        raise ValueError('Immutable revision descriptor differs')
    if not target.exists():
        _write_json_atomic(target, descriptor)
    # Round-trip every captured revision before marking it ready.
    if _read_revision(directory, revision) != files:
        raise ValueError('Revision reconstruction differs')


def _read_revision(directory, revision):
    directory = Path(directory)
    if not _valid_sha(revision):
        raise ValueError('Invalid revision ID')
    descriptor = directory / 'revisions-v2' / (revision + '.json')
    if not descriptor.exists():
        catalog = directory / 'history-v1' / 'catalog.json'
        if catalog.exists():
            record = json.loads(catalog.read_text()).get('revisions', {}).get(revision)
            if record is not None:
                if __package__:
                    from .curation_history import read_revision
                else:
                    from curation_history import read_revision
                sha = record['pack']
                if not _valid_sha(sha):
                    raise ValueError('Unsafe historical pack ID')
                pack = directory / 'history-v1' / 'packs' / (sha + '.sqlite')
                if digest(pack) != sha:
                    raise ValueError('Historical pack checksum mismatch')
                return read_revision(pack, revision)
        with zipfile.ZipFile(directory / 'revisions' / (revision + '.zip')) as archive:
            return {name: archive.read(name) for name in archive.namelist()}
    record = json.loads(descriptor.read_text())
    if record.get('schema_version') != 2 or record.get('revision') != revision:
        raise ValueError('Invalid revision descriptor')
    result = {}
    allowed = {'archive.json', 'bundle.json', 'reviews.json', 'manifest.json', 'training-feedback.json', 'chip-tags.json'}
    for name, entry in record['files'].items():
        if name not in allowed:
            raise ValueError('Unexpected revision file')
        if not _valid_sha(entry['sha256']):
            raise ValueError('Unsafe file ID')
        if 'chunks' not in entry:
            index_id = entry.get('index_sha256',entry['sha256'])
            if not _valid_sha(index_id):
                raise ValueError('Unsafe file index ID')
            index = json.loads((directory / 'file-index' / (index_id + '.json')).read_text())
            if 'index_sha256' in entry and hashlib.sha256(canonical(index)).hexdigest() != index_id:
                raise ValueError('File index checksum mismatch')
            if index['sha256'] != entry['sha256'] or index['size'] != entry['size']:
                raise ValueError('File index differs from revision')
            entry = index
        parts = []
        for sha in entry['chunks']:
            if not _valid_sha(sha):
                raise ValueError('Unsafe chunk ID')
            part = zlib.decompress((directory / 'chunks' / (sha + '.zlib')).read_bytes())
            if hashlib.sha256(part).hexdigest() != sha:
                raise ValueError('Restored chunk checksum mismatch')
            parts.append(part)
        content = b''.join(parts)
        if len(content) != entry['size'] or hashlib.sha256(content).hexdigest() != entry['sha256']:
            raise ValueError('Restored annotation checksum mismatch')
        result[name] = content
    return result


def _progress(directory, stage, **extra):
    _write_json_atomic(Path(directory) / 'progress.json', {'stage': stage, 'at': datetime.now(UTC).isoformat(), **extra})


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
    paths, pending = set(), [raster]
    while pending:
        current = pending.pop().resolve()
        if current in paths:
            continue
        paths.add(current)
        if current.suffix.lower() == '.vrt':
            tree = ET.parse(current)
            for element in tree.findall('.//SourceFilename') + tree.findall('.//SourceDataset'):
                source = Path(element.text)
                if element.get('relativeToVRT') == '1':
                    source = current.parent / source
                pending.append(source.resolve())
    paths = sorted(paths)
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
    # Tags belong to the acquisition workspace, whose name can differ from
    # the grouped review_id used for the annotation bundle.
    tags_path = bundle_dir.parent / review_dir.name / 'chip-tags.json'
    if not tags_path.exists():
        tags_path = bundle_dir / 'chip-tags.json'  # older colocated bundles
    if tags_path.exists():
        files['chip-tags.json'] = tags_path.read_bytes()
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
        "storage_format": "chunks-v2",
        "city": bundle["city"].lower(),
        "review_id": bundle["review_id"],
        "acquisition_id": source["acquisition_id"],
        "images": images,
        "manifest_sha256": bundle["source_manifest_sha256"],
        "review_state_sha256": bundle["review_state_sha256"],
        "feedback_current": bundle["feedback_current"],
    }
    if 'chip-tags.json' in files:
        identity['chip_tags_sha256'] = hashlib.sha256(files['chip-tags.json']).hexdigest()
    # Semantic review hashes exclude save/export timestamps. Bind the exact
    # packet too, so regenerated files get a new immutable revision.
    identity['snapshot_files_sha256'] = {
        name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())
    }
    revision = hashlib.sha256(canonical(identity)).hexdigest()
    identity["revision"] = revision
    files["archive.json"] = canonical(identity)
    _store_revision(directory, revision, files)
    _write_json_atomic(directory / "latest" / f"{identity['city']}.json", identity)
    _write_json_atomic(directory / "latest" / f"{workspace_status_key(identity['city'], identity['review_id'])}.json", identity)
    return identity


def command(*args, input=None):
    environment = os.environ.copy()
    if os.name == 'nt' and Path(str(args[0])).name.lower() in {'gcloud', 'gcloud.cmd'}:
        # Bound the SDK's Windows worker pool for large numbers of small files.
        environment['CLOUDSDK_STORAGE_PROCESS_COUNT'] = '1'
        environment['CLOUDSDK_STORAGE_THREAD_COUNT'] = '2'
        if '--checksums-only' in args:
            # Small revision files are cheaper to hash in-process than with a binary per file.
            environment['CLOUDSDK_STORAGE_USE_GCLOUD_CRC32C'] = 'false'
            environment['CLOUDSDK_STORAGE_CHECK_HASHES'] = 'always'
    return subprocess.check_output(list(map(str, args)), text=True, input=input,
                                   stderr=subprocess.PIPE, env=environment).strip()


def upload_file(path, url):
    # Never delete/replace an immutable object. gcloud validates transfer checksums.
    command(
        "gcloud.cmd" if os.name == "nt" else "gcloud", "storage", "cp", "--no-clobber", path, url
    )


def backup_workspaces(root, annotations):
    """Include initialized NYC acquisitions, rather than one hardcoded vintage."""
    workspaces=[(city,review,root/'imagery'/city/year/(review+'.vrt'))
        for city,review,year in [('ussfo','ussfo-2022-mosaic','2022'),('usbos','usbos-2023-external','2023')]]
    legacy='usnyc-2022-cold-start-30cm'
    legacy_manifest = root/'qa/registration'/legacy/'manifest.json'
    if legacy_manifest.is_file():
        review_id = json.loads(legacy_manifest.read_text())['metadata']['review_id']
        if (annotations/'usnyc'/review_id/'bundle.json').is_file():
            workspaces.append(('usnyc',legacy,root/'imagery/usnyc/2022'/(legacy+'.vrt')))
    for manifest_path in sorted((root/'qa/registration').glob('*/manifest.json')):
        if any(review==manifest_path.parent.name for _,review,_ in workspaces):
            continue
        metadata=json.loads(manifest_path.read_text())['metadata']
        inventory=str(metadata.get('inventory','')).replace('\\','/').lower()
        if metadata.get('city') == 'usffx' and '/inventory/usffx/' in inventory:
            if (annotations/'usffx'/metadata['review_id']/'bundle.json').is_file():
                raster = Path(metadata['source_raster']).resolve()
                if not raster.is_relative_to(root.resolve()):
                    raise ValueError('Fairfax review raster escapes artifact root')
                workspaces.append(('usffx', manifest_path.parent.name, raster))
            continue
        if '/inventory/usnyc/' not in inventory:
            continue
        if not (annotations/'usnyc'/metadata['review_id']/'bundle.json').is_file():
            continue  # An unopened acquisition has no saved annotations to back up.
        source=str(metadata['source_raster']).replace('\\','/')
        marker='/imagery/usnyc/'
        if marker not in source:
            raise ValueError('NYC review raster is outside the NYC imagery namespace')
        raster=root/'imagery/usnyc'/source.split(marker,1)[1]
        if not raster.resolve().is_relative_to((root/'imagery/usnyc').resolve()):
            raise ValueError('NYC review raster escapes imagery root')
        workspaces.append(('usnyc',manifest_path.parent.name,raster))
    registry = root/'qa/temporal-acquisitions.json'
    if registry.exists():
        for item in json.loads(registry.read_text(encoding='utf-8'))['acquisitions']:
            if item.get('state') != 'ready':
                continue
            review_dir = Path(item['review_dir']).resolve()
            raster = Path(item['raster']).resolve()
            if review_dir.parent != (root/'qa/registration').resolve() or not raster.is_relative_to(root.resolve()):
                raise ValueError('Temporal backup workspace escapes artifact root')
            if any(review == review_dir.name for _, review, _ in workspaces):
                continue
            metadata = json.loads((review_dir/'manifest.json').read_text(encoding='utf-8'))['metadata']
            if (annotations/item['city']/metadata['review_id']/'bundle.json').is_file():
                workspaces.append((item['city'], review_dir.name, raster))
    return workspaces


def backup(root, annotations, bucket, capture_lock=None):
    root, annotations = Path(root), Path(annotations)
    directory = archive_root(root)
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / "backup.lock"
    with lock.open("x") as handle:
        json.dump({"pid": os.getpid(), "started_at": datetime.now(UTC).isoformat()}, handle)
    try:
        _progress(directory, 'Capturing and verifying current annotations')
        snapshots = []
        for city, review, raster in backup_workspaces(root,annotations):
            review_dir = root / "qa" / "registration" / review
            manifest = json.loads((review_dir / "manifest.json").read_text())
            bundle_dir = annotations / city / manifest["metadata"]["review_id"]
            with capture_lock or nullcontext():
                snapshots.append(capture(root, bundle_dir, review_dir, raster, verify_images=True))
        # Publish stable copies through an isolated clone, never git-add the live autosave files.
        publisher = directory / "publisher"
        _progress(directory, 'Backing up annotations to Git')
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
            files = _read_revision(directory, snapshot['revision'])
            for name in [
                    "bundle.json",
                    "manifest.json",
                    "reviews.json",
                    "training-feedback.json",
                    "chip-tags.json",
            ]:
                if name in files:
                    (target / name).write_bytes(files[name])
                elif name in {"training-feedback.json", "chip-tags.json"}:
                    (target / name).unlink(missing_ok=True)
        runner = publisher / "tools" / "curation_archive.py"
        runner.parent.mkdir(parents=True, exist_ok=True)
        runner.write_bytes(Path(__file__).read_bytes())
        history_runner = publisher / 'tools' / 'curation_history.py'
        history_runner.write_bytes(Path(__file__).with_name('curation_history.py').read_bytes())
        command(
            "git",
            "-C",
            publisher,
            "add",
            "--",
            "annotations",
            ".gitattributes",
            "tools/curation_archive.py",
            "tools/curation_history.py",
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
            files = _read_revision(directory, snapshot['revision'])
            bundle = json.loads(files['bundle.json'])
            expected_files = dict(bundle['files'])
            if 'chip-tags.json' in files:
                expected_files['chip-tags.json'] = {'sha256': snapshot['chip_tags_sha256']}
            for name, expected in expected_files.items():
                git_path = f"annotations/{snapshot['city']}/{snapshot['review_id']}/{name}"
                content = subprocess.check_output(
                    ["git", "-C", str(publisher), "show", f"{commit}:{git_path}"]
                )
                if hashlib.sha256(content).hexdigest() != expected["sha256"]:
                    raise ValueError("Committed annotation checksum mismatch")
        for snapshot in snapshots:
            _write_json_atomic(
                directory / "git-status" / f"{workspace_status_key(snapshot['city'], snapshot['review_id'])}.json",
                {**snapshot, "commit": commit, "at": datetime.now(UTC).isoformat()},
            )
        # Legacy ZIP history remains intact locally/remotely, but is no longer
        # bulk-uploaded. All new history uses independent deduplicated manifests.
        _progress(directory, 'Backing up source imagery')
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
        _progress(directory, 'Backing up annotation history')
        _sync_history(directory, bucket, capture_lock)
        for snapshot in snapshots:
            _progress(directory, 'Publishing verified backup catalog')
            key = workspace_status_key(snapshot['city'], snapshot['review_id'])
            receipt = directory / "remote-status" / f"{key}.json"
            pending_receipt = receipt.with_suffix('.pending.json')
            _write_json_atomic(pending_receipt, {**snapshot, "bucket": bucket, "at": datetime.now(UTC).isoformat()})
            gc = "gcloud.cmd" if os.name == "nt" else "gcloud"
            command(
                gc,
                "storage",
                "cp",
                pending_receipt,
                f"{bucket}/catalogs/{snapshot['city']}/{snapshot['revision']}.json",
            )
            command(gc, "storage", "cp", pending_receipt, f"{bucket}/latest/by-review/{key}.json")
            # Retain the original city's legacy restore entry point, without
            # letting a later acquisition overwrite it in this backup batch.
            if snapshot is next(s for s in snapshots if s['city'] == snapshot['city']):
                command(gc, "storage", "cp", pending_receipt, f"{bucket}/latest/{snapshot['city']}.json")
            _replace_with_retry(pending_receipt, receipt)
        _progress(directory, 'Complete')
        print("Annotation Git push and private object backup completed", flush=True)
    except Exception as error:
        detail = {'error': str(error)}
        if isinstance(error, subprocess.CalledProcessError):
            diagnostics = directory / 'last-command-failure.json'
            _write_json_atomic(diagnostics, {'command': error.cmd, 'exit_code': error.returncode,
                                             'stderr': error.stderr, 'at': datetime.now(UTC).isoformat()})
            detail['diagnostics'] = str(diagnostics)
        _progress(directory, 'Failed', **detail)
        raise
    finally:
        lock.unlink(missing_ok=True)


def status(root, city, state_revision, feedback_current, manifest_revision=None, chip_tags_revision=None, review_id=None):
    directory = archive_root(root)
    result = {"local_revision": state_revision, "published_for_training": feedback_current}
    key = workspace_status_key(city, review_id)
    error = directory / "errors" / f"{key}.json"
    result["archive_error"] = json.loads(error.read_text()).get("error") if error.exists() else None
    for name, subdir in [("remote", "remote-status"), ("git", "git-status")]:
        path = directory / subdir / f"{key}.json"
        saved = json.loads(path.read_text()) if path.exists() else {}
        if not saved and review_id:
            legacy = directory / subdir / f"{city}.json"
            candidate = json.loads(legacy.read_text()) if legacy.exists() else {}
            if candidate.get('review_id') == review_id:
                saved = candidate
        result[name] = {
            **{k: v for k, v in saved.items() if k != 'images'},
            "image_count": len(saved.get('images', {})),
            "current": (
                saved.get("review_state_sha256") == state_revision
                and (manifest_revision is None or saved.get("manifest_sha256") == manifest_revision)
                and saved.get('chip_tags_sha256') == chip_tags_revision
            ),
        }
    return result


def _sync_history(directory, bucket, capture_lock=None):
    # Freeze the manifest set BEFORE uploading dependencies. A concurrent save
    # may add chunks, but cannot publish a manifest whose chunks were missed.
    with tempfile.TemporaryDirectory(prefix='curation-backup-manifests-') as temporary:
        stage = Path(temporary)
        with capture_lock or nullcontext():
            for descriptor in (directory / 'revisions-v2').glob('*.json'):
                shutil.copy2(descriptor, stage / descriptor.name)
        _pack_history(directory, stage)
        for category in ['images', 'packs', 'file-index', 'revisions-v2', 'bindings']:
            folder = directory / category
            options = ['--recursive', '--exclude=.*\\.tmp$']
            if category == 'revisions-v2':
                folder = stage
                # Compare packed content even when a source timestamp matches remotely.
                options.append('--checksums-only')
            folder.mkdir(parents=True, exist_ok=True)
            inventory = [p for p in folder.rglob('*') if p.is_file() and not p.name.endswith('.tmp')]
            _progress(directory, 'Syncing ' + category, files=len(inventory), bytes=sum(p.stat().st_size for p in inventory))
            command('gcloud.cmd' if os.name == 'nt' else 'gcloud', 'storage', 'rsync', folder,
                    bucket.rstrip('/') + '/' + category, *options)


def _pack_history(directory, stage):
    """Pack only previously unpacked chunks; publish exact pack dependencies.

    A local index is acceleration metadata, not required for a remote restore.
    Remote revision descriptors contain the needed pack hashes directly.
    """
    index_path = directory / 'pack-index.json'
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    records = {}
    file_chunks = {}
    validated_chunks = set()
    descriptors = list(stage.glob('*.json'))
    for number, path in enumerate(descriptors, start=1):
        record = json.loads(path.read_text())
        required = set()
        for entry in record['files'].values():
            if 'chunks' in entry:
                required.update(entry['chunks'])
            else:
                index_id = entry.get('index_sha256', entry['sha256'])
                if index_id not in file_chunks:
                    content = json.loads((directory/'file-index'/(index_id+'.json')).read_text())
                    file_chunks[index_id] = frozenset(content['chunks'])
                required.update(file_chunks[index_id])
        unchecked = required - validated_chunks
        if not all(_valid_sha(sha) for sha in unchecked):
            raise ValueError('Unsafe chunk ID')
        validated_chunks.update(unchecked)
        records[path] = (record, required)
        if number % 1000 == 0 or number == len(descriptors):
            _progress(directory, 'Preparing annotation history', revisions_done=number,
                      revisions_total=len(descriptors))
    needed = set().union(*(required for _,required in records.values()))
    folder = directory / 'packs'; folder.mkdir(exist_ok=True)
    existing = {index[sha] for sha in needed if sha in index}
    for pack in existing:
        if not _valid_sha(pack) or digest(folder/(pack+'.zip')) != pack:
            raise ValueError('Missing or corrupt annotation pack')
    pending = sorted(needed-index.keys())
    while pending:
        batch, size = [], 0
        while pending and size < 4*1024*1024:
            sha = pending.pop()
            batch.append(sha)
            size += (directory/'chunks'/(sha+'.zlib')).stat().st_size
        temporary = folder/('.'+uuid4().hex+'.tmp')
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
                for sha in batch:
                    content = (directory/'chunks'/(sha+'.zlib')).read_bytes()
                    if hashlib.sha256(zlib.decompress(content)).hexdigest() != sha:
                        raise ValueError('Corrupt annotation chunk before packing')
                    archive.writestr(sha+'.zlib', content)
            with temporary.open('r+b') as stream:
                os.fsync(stream.fileno())
            pack = digest(temporary)
            os.replace(temporary, folder/(pack+'.zip'))
        finally:
            temporary.unlink(missing_ok=True)
        index.update({sha:pack for sha in batch})
        _write_json_atomic(index_path,index)
    for path,(record,required) in records.items():
        record['packs'] = sorted({index[sha] for sha in required})
        original_stat = path.stat()
        _write_json_atomic(path,record)
        # rsync patches remote mtimes even for checksum-identical files. Keep the
        # immutable descriptor timestamp copied into staging across each backup.
        os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))


def restore(root, revision, destination, bucket, include_sources=False):
    """Restore to a NEW directory; never touch live review state. Verify every fetched object."""
    if len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Invalid revision ID")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    gc = "gcloud.cmd" if os.name == "nt" else "gcloud"
    descriptor = destination / 'revisions-v2' / (revision + '.json')
    descriptor.parent.mkdir()
    try:
        command(gc, 'storage', 'cp', f'{bucket}/revisions-v2/{revision}.json', descriptor)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        # Only a missing descriptor means legacy format, not a network/auth failure.
        if isinstance(error, subprocess.CalledProcessError) and not any(
            text in (error.stderr or '').lower() for text in ('no urls matched', 'matched no objects or files', 'not found', '404')
        ):
            raise
        catalog = destination / 'history-v1' / 'catalog.json'
        catalog.parent.mkdir()
        try:
            command(gc, 'storage', 'cp', f'{bucket}/history-v1/catalog.json', catalog)
        except (FileNotFoundError, subprocess.CalledProcessError) as history_error:
            if isinstance(history_error, subprocess.CalledProcessError) and not any(
                text in (history_error.stderr or '').lower() for text in ('no urls matched', 'matched no objects or files', 'not found', '404')
            ):
                raise
            historical = None
        else:
            historical = json.loads(catalog.read_text()).get('revisions', {}).get(revision)
        if historical is not None:
            sha = historical['pack']
            if not _valid_sha(sha):
                raise ValueError('Unsafe historical pack ID')
            packs = catalog.parent / 'packs'
            packs.mkdir()
            command(gc, 'storage', 'cp', f'{bucket}/history-v1/packs/{sha}.sqlite', packs / (sha + '.sqlite'))
        else:
            packet = destination / 'revisions' / (revision + '.zip')
            packet.parent.mkdir()
            command(gc, 'storage', 'cp', f'{bucket}/revisions/{revision}.zip', packet)
    else:
        record = json.loads(descriptor.read_text())
        indexes = {entry.get('index_sha256',entry['sha256']) for entry in record['files'].values() if 'chunks' not in entry}
        if not all(_valid_sha(sha) for sha in indexes):
            raise ValueError('Unsafe file ID')
        index_dir = destination / 'file-index'
        index_dir.mkdir()
        if indexes:
            command(gc, 'storage', 'cp', '--read-paths-from-stdin', index_dir,
                    input=''.join(f'{bucket}/file-index/{sha}.json\n' for sha in sorted(indexes)))
        entries = [entry if 'chunks' in entry else json.loads((index_dir/(entry.get('index_sha256',entry['sha256'])+'.json')).read_text())
                   for entry in record['files'].values()]
        parts = sorted({sha for entry in entries for sha in entry['chunks']})
        if not all(_valid_sha(sha) for sha in parts):
            raise ValueError('Unsafe chunk ID')
        chunks_dir = destination / 'chunks'
        chunks_dir.mkdir()
        if 'packs' in record:
            packs = record['packs']
            if not all(_valid_sha(sha) for sha in packs):
                raise ValueError('Unsafe pack ID')
            pack_dir=destination/'packs';pack_dir.mkdir()
            if packs:
                command(gc,'storage','cp','--read-paths-from-stdin',pack_dir,
                        input=''.join(f'{bucket}/packs/{sha}.zip\n' for sha in packs))
            required=set(parts)
            for sha in packs:
                packet=pack_dir/(sha+'.zip')
                if digest(packet)!=sha:raise ValueError('Restored pack checksum mismatch')
                with zipfile.ZipFile(packet) as archive:
                    for part in required.intersection(name.removesuffix('.zlib') for name in archive.namelist()):
                        (chunks_dir/(part+'.zlib')).write_bytes(archive.read(part+'.zlib'))
        elif parts:
            command(gc, 'storage', 'cp', '--read-paths-from-stdin', chunks_dir,
                    input=''.join(f'{bucket}/chunks/{sha}.zlib\n' for sha in parts))
    files = _read_revision(destination, revision)
    info = json.loads(files['archive.json'])
    identity = {k: v for k, v in info.items() if k != "revision"}
    if info['revision'] != revision or hashlib.sha256(canonical(identity)).hexdigest() != revision:
        raise ValueError('Revision identity mismatch')
    bundle = json.loads(files['bundle.json'])
    for name, expected in bundle['files'].items():
        if name not in {'manifest.json', 'reviews.json', 'training-feedback.json'}:
            raise ValueError('Unexpected revision file')
        content = files[name]
        if hashlib.sha256(content).hexdigest() != expected['sha256']:
            raise ValueError('Restored annotation checksum mismatch')
        (destination / name).write_bytes(content)
    for name in ['archive.json', 'bundle.json']:
        (destination / name).write_bytes(files[name])
    if 'chip-tags.json' in files:
        if hashlib.sha256(files['chip-tags.json']).hexdigest() != info.get('chip_tags_sha256'):
            raise ValueError('Restored chip tags differ from revision')
        (destination / 'chip-tags.json').write_bytes(files['chip-tags.json'])
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
    image_ids = sorted(set(info['images'].values()))
    if not all(_valid_sha(sha) for sha in image_ids):
        raise ValueError('Unsafe image ID')
    if image_ids:
        command(gc, 'storage', 'cp', '--read-paths-from-stdin', image_objects,
                input=''.join(f'{bucket}/images/{sha}\n' for sha in image_ids))
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
