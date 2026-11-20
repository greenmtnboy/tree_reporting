"""Freeze Fairfax curation and make its existing imagery references portable."""
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
from rasterio.windows import Window

from urban_tree_ml.fairfax_curation import freeze_training, validate_admission
from urban_tree_ml.prepared_finetune import sha


def prepare(root, inputs, taxonomy):
    with tempfile.TemporaryDirectory(prefix='fairfax-joint-') as temp:
        frozen = Path(temp)/'frozen'
        receipt = freeze_training(root,frozen,taxonomy,any_curation=True)
        raw = json.loads((frozen/'config.json').read_text())
        shutil.copytree(frozen/'usffx',inputs/'usffx')
    directory = inputs/'usffx'
    policy = json.loads((directory/'fairfax-admission.json').read_text())
    original = Path(raw['imagery']['local_raster'])
    portable = directory/original.name
    tree = ET.parse(original)
    expected = {Path(item['path']).resolve(): item['sha256'] for item in policy['source_files']}
    found = set()
    for element in tree.findall('.//SourceFilename'):
        path = Path(element.text)
        if element.get('relativeToVRT') == '1':
            path = original.parent/path
        path = path.resolve()
        if path not in expected or not path.is_relative_to(root):
            raise ValueError('Unverified Fairfax VRT dependency')
        found.add(path)
        element.text = os.path.relpath(path,portable.parent).replace('\\','/')
        element.set('relativeToVRT','1')
    if found != set(expected):
        raise ValueError('Fairfax source receipt and VRT disagree')
    tree.write(portable,encoding='utf-8',xml_declaration=True)
    with rasterio.open(original) as old, rasterio.open(portable) as new:
        assert old.crs == new.crs and old.transform == new.transform and old.shape == new.shape
        for chips in policy['admitted'].values():
            for chip in chips:
                row,col=int(chip[1:7])*512,int(chip.split('_c')[1])*512
                window=Window(col,row,512,512)
                if not np.array_equal(old.read(window=window),new.read(window=window)):
                    raise ValueError('Portable Fairfax imagery changed pixels')
    policy.update(portable_root_relative=True, original_raster_sha256=policy['raster_sha256'],
        raster=portable.relative_to(root).as_posix(), raster_sha256=sha(portable),
        source_files=[dict(path=p.relative_to(root).as_posix(),sha256=expected[p]) for p in sorted(found)])
    (directory/'fairfax-admission.json').write_text(json.dumps(policy,indent=2)+'\n')
    raw['imagery']['local_raster']=policy['raster']
    raw['reference']['taxonomy_path']=taxonomy.relative_to(root).as_posix()
    raw['reference']['normalization_path']=Path(raw['reference']['normalization_path']).relative_to(root).as_posix()
    raw['inventory']['parquet_url']=(directory/'inventory.parquet').relative_to(root).as_posix()
    (inputs/'fairfax-joint.json').write_text(json.dumps(dict(config=raw,test_evaluated=False),indent=2)+'\n')
    validate_admission(directory)
    assets={p.relative_to(root).as_posix():expected[p] for p in sorted(found)}
    archive=root/(inputs.name+'-assets.tar')
    if archive.exists():
        raise ValueError('Run asset archive already exists')
    with tarfile.open(archive,'w') as target:
        for p in sorted(found):
            target.add(p,arcname=p.relative_to(root).as_posix(),recursive=False)
    (root/(inputs.name+'-assets.json')).write_text(json.dumps(assets,indent=2)+'\n')
    return raw,receipt
