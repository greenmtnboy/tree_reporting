"""Install a checked artifact-root-relative archive without overwriting other data."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tarfile
import tempfile


def destination_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name or not relative.parts:
        raise ValueError('Unsafe asset member')
    destination = root.joinpath(*relative.parts)
    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError('Asset escapes artifact root')
    return destination


def check_writable(root, receipt_path):
    """Probe actual asset ancestors before uploading gigabytes, without chmod."""
    receipt = json.loads(receipt_path.read_text())
    checked = set()
    for name in receipt:
        destination = destination_path(root, name)
        if destination.exists():
            with destination.open('rb') as existing:
                existing.read(1)
            continue
        parent = destination.parent
        while not parent.exists():
            parent = parent.parent
        if parent in checked:
            continue
        with tempfile.TemporaryFile(dir=parent) as probe:
            probe.write(b'asset-storage-health'); probe.flush(); os.fsync(probe.fileno())
            probe.seek(0)
            assert probe.read() == b'asset-storage-health'
        checked.add(parent)
    print('Frozen asset destination permissions verified:', len(checked), flush=True)


def install(root, archive_path, receipt_path):
    receipt=json.loads(receipt_path.read_text())
    with tarfile.open(archive_path) as archive:
        members=archive.getmembers()
        if {m.name for m in members}!=set(receipt) or len(members)!=len(receipt):
            raise ValueError('Asset archive membership differs from frozen receipt')
        for member in members:
            if not member.isfile():
                raise ValueError('Unsafe asset member')
            destination=destination_path(root, member.name)
            expected=receipt[member.name]
            if destination.exists():
                with destination.open('rb') as current:
                    digest=hashlib.sha256()
                    while chunk:=current.read(8*1024*1024):digest.update(chunk)
                    actual=digest.hexdigest()
                if actual!=expected:
                    raise ValueError('Refusing to overwrite differing artifact: '+member.name)
                continue
            destination.parent.mkdir(parents=True,exist_ok=True)
            partial=destination.with_name(destination.name+'.install-part')
            digest=hashlib.sha256()
            with archive.extractfile(member) as source, partial.open('wb') as output:
                while chunk:=source.read(8*1024*1024):
                    digest.update(chunk);output.write(chunk)
            if digest.hexdigest()!=expected:
                raise ValueError('Asset payload hash mismatch')
            partial.replace(destination)
    print('Verified frozen assets installed:',len(receipt),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--check-writable',action='store_true')
    args=parser.parse_args()
    if args.check_writable:
        check_writable(args.root,args.receipt)
    elif args.archive is None:
        parser.error('--archive is required unless --check-writable is used')
    else:
        install(args.root,args.archive,args.receipt)
