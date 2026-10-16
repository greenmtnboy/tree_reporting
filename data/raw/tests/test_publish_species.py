import copy
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'website'))
from publish_species import INDEX, PublishError, publish, release, validate


def catalogue():
    return {
        'schema_version': 1, 'generated_at': '2026-10-16T12:00:00+00:00',
        'top_n': 2000, 'species_count_total': 1, 'genus_count_total': 1,
        'named_trees_total': 10, 'named_trees_in_catalogue': 10,
        'cities': [{'code': 'USAAA', 'named_trees': 10}],
        'slug_registry': {'Quercus robur': 'quercus-robur'},
        'species': [{
            'species': 'Quercus robur', 'slug': 'quercus-robur', 'genus': 'Quercus',
            'rank': 1, 'genus_rank': 1, 'genus_trees': 10, 'trees': 10,
            'city_count': 1, 'cities': [{'code': 'USAAA', 'trees': 10}],
            'common_name': 'English oak', 'photo': None,
            'traits': {'tree_form': 'broadleaf', 'is_evergreen': False},
        }],
    }


def test_release_digest_and_record_share_one_revision():
    c = catalogue()
    validate(c)
    index, objects = release(c)
    body = next(body for name, body in objects.items() if name.endswith('/catalogue.json'))
    assert hashlib.sha256(body).hexdigest() == index['revision']
    record = json.loads(next(body for name, body in objects.items() if name.endswith('/quercus-robur.json')))
    assert record['revision'] == index['revision']
    assert record['generated_at'] == index['generated_at']
    assert record['species'] == c['species'][0]


def test_publish_index_only_after_all_records_and_preserve_its_generation():
    index, objects = release(catalogue())
    client = Mock()
    client.generations.return_value = {}
    publish(client, index, objects, 123)
    calls = client.put.call_args_list
    assert len(calls) == len(objects) + 1
    assert calls[-1].args[1] == INDEX
    assert calls[-1].kwargs['if_generation_match'] == 123
    assert all(c.kwargs['if_generation_match'] == 0 for c in calls[:-1])


def test_failed_record_upload_never_moves_live_index():
    index, objects = release(catalogue())
    client = Mock()
    client.generations.return_value = {}
    client.put.side_effect = PublishError('network failure')
    with pytest.raises(PublishError):
        publish(client, index, objects, 123)
    assert all(c.args[1] != INDEX for c in client.put.call_args_list)


def test_resume_skips_existing_immutable_records():
    index, objects = release(catalogue())
    client = Mock()
    client.generations.return_value = {name: 1 for name in objects}
    publish(client, index, objects, 123)
    assert client.put.call_count == 1
    assert client.put.call_args.args[1] == INDEX


@pytest.mark.parametrize('change', ['missing', 'city-total', 'unsafe-slug', 'genus-rank'])
def test_incomplete_or_inconsistent_release_is_rejected(change):
    c = copy.deepcopy(catalogue())
    if change == 'missing': c['species'] = []
    if change == 'city-total': c['species'][0]['cities'][0]['trees'] = 9
    if change == 'unsafe-slug': c['slug_registry']['Quercus robur'] = '../escape'
    if change == 'genus-rank': c['species'][0]['genus_rank'] = 2
    with pytest.raises(PublishError): validate(c)
