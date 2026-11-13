#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# ///
"""Verify or restore an immutable environmental-context bundle (no portal calls)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from urllib.request import urlopen


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')


def safe_path(root, name):
    """Reject traversal, drive paths, alternate streams and symlink escapes."""
    p = PurePosixPath(name)
    if (not name or p.as_posix() != name or p.is_absolute() or '..' in p.parts
            or any(not re.fullmatch(r'[A-Za-z0-9_.-]+', part) for part in p.parts)):
        raise ValueError(f'Unsafe bundle path: {name!r}')
    root = Path(root).resolve()
    path = (root / str(p)).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f'Bundle path escapes root: {name!r}')
    return path


def validate_manifest(manifest):
    if manifest.get('schema_version') != 1 or not manifest.get('assets'):
        raise ValueError('Expected context bundle schema 1 with assets')
    seen = set()
    for name, asset in manifest['assets'].items():
        safe_path(Path.cwd(), name)
        sha = asset['sha256']
        if len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError(f'Invalid checksum: {name}')
        if not isinstance(asset['bytes'], int) or asset['bytes'] <= 0:
            raise ValueError(f'Invalid asset length: {name}')
        target = asset.get('restore_to')
        if target:
            safe_path(Path.cwd(), target)
            # A downloaded bundle must never overwrite code or repository config.
            if not (target.startswith('.tmp/') or target.startswith('scripts/boston_canopy_renders/')):
                raise ValueError(f'Unapproved restore destination: {target}')
            if target in seen:
                raise ValueError(f'Duplicate restore destination: {target}')
            seen.add(target)


def verify(bundle):
    bundle = Path(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    validate_manifest(manifest)
    for name, asset in manifest['assets'].items():
        path = safe_path(bundle, name)
        if path.stat().st_size != asset['bytes'] or digest(path) != asset['sha256']:
            raise ValueError(f'Checksum/size mismatch: {name}')
    return manifest


def restore(bundle, checkout):
    """Validate everything before writing; refuse to replace different local inputs."""
    manifest = verify(bundle)
    for name, expected in manifest.get('code_sha256_lf', {}).items():
        path = safe_path(checkout, name)
        actual = hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if actual != expected:
            raise ValueError(f'Renderer differs from this release: {name}')
    pending = []
    for name, asset in manifest['assets'].items():
        if not asset.get('restore_to'):
            continue
        target = safe_path(checkout, asset['restore_to'])
        if target.exists() and digest(target) != asset['sha256']:
            raise ValueError(f'Different local input exists; use a fresh checkout: {target}')
        pending.append((safe_path(bundle, name), target))
    for source, target in pending:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + '.context-pending')
            shutil.copyfile(source, temporary)
            temporary.replace(target)
    return manifest


def fetch(url, output):
    """The manifest URL embeds its SHA-256; verify downloads before exposing them."""
    if not url.startswith('https://') or not url.endswith('/manifest.json'):
        raise ValueError('Use an HTTPS immutable release manifest URL')
    revision = url.split('/')[-2]
    output = Path(output)
    if output.exists():
        raise ValueError('Download destination must be new')
    with urlopen(url, timeout=120) as response:
        body = response.read()
    if hashlib.sha256(body).hexdigest() != revision:
        raise ValueError('Manifest URL revision does not match its SHA-256')
    manifest = json.loads(body)
    validate_manifest(manifest)
    output.mkdir(parents=True)
    for name, asset in manifest['assets'].items():
        target = safe_path(output, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.pending')
        with urlopen(url.rsplit('/', 1)[0] + '/' + name, timeout=180) as response, temporary.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        if temporary.stat().st_size != asset['bytes'] or digest(temporary) != asset['sha256']:
            raise ValueError(f'Download checksum mismatch: {name}')
        temporary.replace(target)
    (output / 'manifest.json').write_bytes(body)  # Commit point, last.
    return verify(output)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    for name in ('verify', 'restore'):
        sub = commands.add_parser(name)
        sub.add_argument('bundle', type=Path)
        if name == 'restore':
            sub.add_argument('--checkout', type=Path, required=True)
    sub = commands.add_parser('fetch')
    sub.add_argument('url')
    sub.add_argument('output', type=Path)
    args = p.parse_args()
    if args.command == 'fetch':
        result = fetch(args.url, args.output)
    elif args.command == 'restore':
        result = restore(args.bundle, args.checkout)
    else:
        result = verify(args.bundle)
    print(f"Verified {result['study']} / {len(result['assets'])} assets")


if __name__ == '__main__':
    main()
