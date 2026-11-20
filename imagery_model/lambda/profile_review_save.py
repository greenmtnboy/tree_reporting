"""Profile a no-op save in a disposable copy, never the live annotation files."""
import argparse
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter

from urban_tree_ml.review_gallery import apply_scene_patch, read_collection, collection_version


def profile(directory):
    directory = Path(directory).resolve()
    before = collection_version(directory)
    with TemporaryDirectory(prefix='review-save-profile-') as temporary:
        copied = Path(temporary)
        for name in ('manifest.json', 'reviews.json'):
            shutil.copyfile(directory / name, copied / name)
        if collection_version(directory) != before:
            raise RuntimeError('Live annotations changed while copying; no profile performed')
        start = perf_counter()
        manifest, state = read_collection(copied)
        load_ms = (perf_counter()-start)*1000
        scene = next(s for s in manifest['scenes'] if s['sample_ids'] and 'test' not in s.get('splits', []))
        trials = []
        for _ in range(2):
            timings = {}
            start = perf_counter()
            result = apply_scene_patch(copied, scene['scene_id'], {'base_revision': state['state_revision']}, timings=timings)
            trials.append(dict(total_ms=round((perf_counter()-start)*1000, 2),
                               phases_ms={k: round(v, 2) for k,v in timings.items()}))
            _, state = read_collection(copied)
        return dict(workspace=directory.name, cold_load_ms=round(load_ms, 2),
                    manifest_samples=len(manifest['samples']), reviews=len(state['reviews']),
                    regions=len(state['mask_regions']), trials=trials,
                    note='No-op saves in disposable copy; excludes live lock wait and browser queue.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    print(json.dumps(profile(parser.parse_args().directory), indent=2))
