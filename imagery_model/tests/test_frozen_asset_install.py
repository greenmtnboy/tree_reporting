import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

def module(name):
    path=Path(__file__).parents[1]/'lambda'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value

def test_install_preserves_existing_and_rejects_payload_drift(tmp_path):
    installer=module('install_frozen_assets')
    root=tmp_path/'root';root.mkdir()
    payload=b'new-year-imagery';name='imagery/usbos/2021/a.tif'
    receipt=tmp_path/'assets.json';receipt.write_text(json.dumps({name:hashlib.sha256(payload).hexdigest()}))
    archive=tmp_path/'assets.tar'
    with tarfile.open(archive,'w') as f:
        info=tarfile.TarInfo(name);info.size=len(payload);f.addfile(info,io.BytesIO(payload))
    installer.check_writable(root,receipt)
    assert list(root.iterdir())==[]
    installer.install(root,archive,receipt)
    installer.install(root,archive,receipt)
    assert (root/name).read_bytes()==payload
    (root/name).write_bytes(b'other')
    with pytest.raises(ValueError,match='Refusing to overwrite'):
        installer.install(root,archive,receipt)
    assert (root/name).read_bytes()==b'other'

@pytest.mark.parametrize('name',['../escape','/absolute','imagery/../../escape','bad\\name'])
def test_permission_probe_rejects_unsafe_paths(tmp_path,name):
    installer=module('install_frozen_assets')
    receipt=tmp_path/'receipt.json';receipt.write_text(json.dumps({name:'hash'}))
    with pytest.raises(ValueError):installer.check_writable(tmp_path,receipt)

def test_destination_probe_failure_prevents_large_upload(tmp_path,monkeypatch):
    launch=module('launch_curated')
    monkeypatch.setattr(launch,'ROOT',tmp_path);monkeypatch.setattr(launch,'RUN','frozen')
    receipt=tmp_path/'frozen-assets.json';receipt.write_text('{}')
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if '--check-writable' in command[-1]:
            assert command[-1].startswith('sudo -n python3 ')
            raise subprocess.CalledProcessError(1,command)
    def output(command,**kwargs):
        local=launch.WORK/'lambda/install_frozen_assets.py' if 'install_frozen_assets.py' in command[-1] else receipt
        return hashlib.sha256(local.read_bytes()).hexdigest()+'  remote\n'
    monkeypatch.setattr(launch.subprocess,'run',run)
    monkeypatch.setattr(launch.subprocess,'check_output',output)
    with pytest.raises(subprocess.CalledProcessError):launch.install_assets(['ssh','host'],[],'host')
    assert not any('prepared-assets.tar' in str(call) for call in calls)
