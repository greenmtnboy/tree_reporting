"""Regenerate one broken derived PNG; never modify curation or source pixels."""
import argparse
import os
import re
import shutil
import tempfile
from datetime import datetime, UTC
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from PIL import Image


def repair(path, raster, chip, size, scale):
    match = re.fullmatch(r'r(\d{6})_c(\d{6})', chip)
    if not match or path.name != f'validation-{chip}.png':
        raise ValueError('Target filename/chip mismatch')
    if not path.is_file():
        raise ValueError('Expected an existing damaged preview')
    try:
        with Image.open(path) as image:
            image.load()
    except OSError:
        pass
    else:
        raise ValueError('Preview decodes successfully; refusing unnecessary overwrite')
    with rasterio.open(raster) as source:
        raw = source.read([1,2,3], window=Window(int(match[2])*size,int(match[1])*size,size,size))
    if raw.shape != (3,size,size):
        raise ValueError('Source window has unexpected size')
    rgb=np.rint(np.clip(np.moveaxis(raw.astype(np.float32)*scale,0,2),0,1)*255).astype(np.uint8)
    backup=path.with_suffix('.png.corrupt-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S'))
    shutil.copy2(path,backup)
    fd,temp=tempfile.mkstemp(prefix=path.stem+'-',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            Image.fromarray(rgb).save(stream,format='PNG',optimize=True)
            stream.flush();os.fsync(stream.fileno())
        with Image.open(temp) as decoded:
            decoded.load()
            assert np.array_equal(np.asarray(decoded),rgb)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    print('Repaired',path,'original preserved at',backup)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--path',type=Path,required=True)
    p.add_argument('--raster',type=Path,required=True);p.add_argument('--chip',required=True)
    p.add_argument('--size',type=int,required=True);p.add_argument('--scale',type=float,required=True)
    a=p.parse_args();repair(a.path,a.raster,a.chip,a.size,a.scale)
