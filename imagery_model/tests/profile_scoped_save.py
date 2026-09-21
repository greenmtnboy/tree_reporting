"""Time a durable patch on disposable copies, never live annotations."""
import json
import shutil
import tempfile
import time
from pathlib import Path

from urban_tree_ml.review_gallery import read_collection, apply_scene_patch

root=Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts/qa/registration')
for name in ['ussfo-2022-mosaic','usbos-2023-external']:
    with tempfile.TemporaryDirectory(prefix='profile-curation-save-') as temp:
        directory=Path(temp)
        for file in ['manifest.json','reviews.json']:
            shutil.copy2(root/name/file,directory/file)
        manifest,state=read_collection(directory)
        scene=manifest['scenes'][-1]
        sample=scene['sample_ids'][0]
        for attempt in range(3):
            start=time.perf_counter()
            result=apply_scene_patch(directory,scene['scene_id'],{'base_revision':state['state_revision'],
                        'reviews':{sample:{'status':'uncertain','note':f'Disposable performance test {attempt}'}}})
            elapsed=time.perf_counter()-start
            _,state=read_collection(directory)
            print(json.dumps({'city':name,'attempt':attempt,'durable_save_seconds':round(elapsed,3),
                              'reviews':result['reviews']}),flush=True)
