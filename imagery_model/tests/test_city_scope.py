import subprocess

from urban_tree_ml.city_scope import publish_cities
from urban_tree_ml.review_gallery import combined_gallery, GALLERY_HTML
from urban_tree_ml.studio_shell import render_studio_shell


def test_publish_all_continues_and_reports_partial_failure():
    calls = []
    def publish(city):
        calls.append(city)
        if city == 'sf':
            raise ValueError('snapshot changed')
    result = publish_cities({'ussfo': 'sf', 'usbos': 'boston'}, publish)
    assert calls == ['sf', 'boston']
    assert result == {'ok': False, 'cities': [
        {'city': 'ussfo', 'ok': False, 'error': 'snapshot changed'}, {'city': 'usbos', 'ok': True}]}
    assert publish_cities({'ussfo': 'sf'}, lambda _: None)['ok']


def test_combined_gallery_has_owner_cities_and_global_pagination():
    scenes = [dict(scene_id=f'scene-{i}', sample_ids=['x'], splits=['train'], tree_count=1, image='x.png') for i in range(8)]
    scenes += [dict(scene_id='scene-test', sample_ids=['x'], splits=['test'])]
    manifest = {'scenes': scenes}
    state = {'scene_reviews': {'scene-0': {'done': True, 'more_done': True}}}
    collections = {city: (manifest, state) for city in ('ussfo', 'usbos')}
    result = combined_gallery(collections, {'page': ['1'], 'status': ['not-final']})
    assert result['total'] == 14 and len(result['items']) == 2
    assert len(set(zip(result['queue_cities'], result['queue']))) == 14
    assert {item['city'] for item in result['items']} == {'ussfo', 'usbos'}


def test_gallery_shell_preserves_pagination_and_generated_scripts_parse():
    import re
    html = render_studio_shell(GALLERY_HTML, 'ussfo', {'ussfo': 'SF', 'usbos': 'Boston'}, {'ussfo': 'v19', 'usbos': 'v19::validation-usbos'})
    assert 'id="prev"' in html and 'id="next"' in html
    for script in re.findall(r'<script>(.*?)</script>', html, re.S):
        subprocess.run(['node', '--check'], input=script, text=True, check=True, capture_output=True)
