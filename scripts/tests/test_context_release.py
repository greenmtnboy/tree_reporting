"""Release integrity, safe replay and publication's manifest-last boundary."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import Mock, patch
import io

import pytest
import pyarrow.parquet as pq
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'data/website')]
from context_release import canonical, digest, fetch, restore, safe_path, verify
from export_boston_context import geoparquet
from publish_context import publish


def fixture_bundle(tmp_path):
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'heat.bin').write_bytes(b'fixed heat values')
    manifest = {'schema_version': 1, 'study': 'test-study', 'assets': {
        'heat.bin': {'sha256': digest(bundle / 'heat.bin'), 'bytes': 17,
                     'restore_to': '.tmp/test/heat.bin'}}}
    (bundle / 'manifest.json').write_bytes(canonical(manifest))
    return bundle, manifest


def test_restore_is_idempotent_and_never_overwrites_different_inputs(tmp_path):
    bundle, _ = fixture_bundle(tmp_path)
    checkout = tmp_path / 'checkout'
    restore(bundle, checkout)
    restore(bundle, checkout)
    target = checkout / '.tmp/test/heat.bin'
    target.write_bytes(b'newer user data')
    with pytest.raises(ValueError, match='Different local input'):
        restore(bundle, checkout)
    assert target.read_bytes() == b'newer user data'


def test_corrupt_bundle_writes_nothing(tmp_path):
    bundle, _ = fixture_bundle(tmp_path)
    (bundle / 'heat.bin').write_bytes(b'corruption')
    with pytest.raises(ValueError, match='mismatch'):
        restore(bundle, tmp_path / 'checkout')
    assert not (tmp_path / 'checkout').exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', 'a\\..\\escape',
                                   'a//b', 'a/./b', 'x?url', 'x#fragment'])
def test_unsafe_asset_paths_are_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        safe_path(tmp_path, name)


def test_manifest_cannot_overwrite_code(tmp_path):
    bundle, manifest = fixture_bundle(tmp_path)
    manifest['assets']['heat.bin']['restore_to'] = 'scripts/renderer.py'
    (bundle / 'manifest.json').write_bytes(canonical(manifest))
    with pytest.raises(ValueError, match='Unapproved'):
        restore(bundle, tmp_path / 'checkout')


def test_code_checks_accept_crlf_but_reject_changed_renderer(tmp_path):
    bundle, manifest = fixture_bundle(tmp_path)
    manifest['code_sha256_lf'] = {'scripts/a.py': hashlib.sha256(b'fixed\n').hexdigest()}
    (bundle / 'manifest.json').write_bytes(canonical(manifest))
    checkout = tmp_path / 'checkout'
    (checkout / 'scripts').mkdir(parents=True)
    (checkout / 'scripts/a.py').write_bytes(b'fixed\r\n')
    restore(bundle, checkout)
    (checkout / 'scripts/a.py').write_bytes(b'changed\n')
    with pytest.raises(ValueError, match='Renderer differs'):
        restore(bundle, checkout)


def test_publish_manifest_last_and_never_commit_failed_upload(tmp_path):
    bundle, _ = fixture_bundle(tmp_path)
    client = Mock()
    client.generations.return_value = {}
    url = publish(bundle, client)
    calls = client.put.call_args_list
    assert calls[-1].args[1].endswith('/manifest.json')
    assert all(c.kwargs['if_generation_match'] == 0 for c in calls)
    assert digest(bundle / 'manifest.json') in url
    client.reset_mock()
    client.put.side_effect = RuntimeError('upload failed')
    with pytest.raises(RuntimeError):
        publish(bundle, client)
    assert not any(c.args[1].endswith('/manifest.json') for c in client.put.call_args_list)


def test_retry_skips_completed_objects(tmp_path):
    bundle, _ = fixture_bundle(tmp_path)
    client = Mock()
    url = publish(bundle)
    prefix = url.split('trilogy_public_models/', 1)[1].rsplit('/', 1)[0]
    client.generations.return_value = {prefix + '/heat.bin': 123}
    publish(bundle, client)
    assert client.put.call_count == 1
    assert client.put.call_args.args[1].endswith('/manifest.json')


def test_fetch_validates_manifest_and_asset_checksums(tmp_path):
    bundle, _ = fixture_bundle(tmp_path)
    body = (bundle / 'manifest.json').read_bytes()
    url = f'https://example.org/releases/{hashlib.sha256(body).hexdigest()}/manifest.json'
    with patch('context_release.urlopen', side_effect=[io.BytesIO(body), io.BytesIO(b'fixed heat values')]):
        fetch(url, tmp_path / 'download')
    verify(tmp_path / 'download')
    with patch('context_release.urlopen', side_effect=[io.BytesIO(body), io.BytesIO(b'corrupt')]):
        with pytest.raises(ValueError, match='Download checksum'):
            fetch(url, tmp_path / 'bad-download')
    assert not (tmp_path / 'bad-download/manifest.json').exists()


def test_geoparquet_retains_optional_metrics_and_crs(tmp_path):
    path = tmp_path / 'cells.parquet'
    geoparquet(path, [{'id': 'missing'}, {'id': 'valid', 'canopy_percent': 43.}],
               [box(0, 0, 500, 500), box(500, 0, 1000, 500)], 32619)
    table = pq.read_table(path)
    assert table['canopy_percent'].to_pylist() == [None, 43.]
    geo = json.loads(table.schema.metadata[b'geo'])
    assert geo['columns']['geometry']['crs']['id']['code'] == 32619
    assert geo['columns']['geometry']['bbox'] == [0, 0, 1000, 500]
