"""Prepare one Boston photo's guarded TRAIN windows for manual component drafts."""
import argparse
import json
import re
from pathlib import Path

import pandas as pd
import rasterio
from PIL import Image
from rasterio.windows import Window

from urban_tree_ml.boundary_shifts import digest, grid, source_members, training_footprint
from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.feedback import _write_json_atomic
from urban_tree_ml.recombinant import workspace, png, SIZES


def prepare(root, run, photo):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', photo):
        raise ValueError('Photo must be a plain acquisition identifier')
    destination = workspace(root)
    if (destination/'source.json').exists():
        pinned = json.loads((destination/'source.json').read_text())
        if pinned['source_id'] == photo:
            raise ValueError('Source already pinned; never replace a component library source in place')
        destination = root/'recombinant'/'sources'/photo
        if (destination/'source.json').exists():
            raise ValueError('Source already pinned; never replace a component library source in place')
    metadata = root/'runs'/run/'evaluation/train-usbos/evaluation-metadata.json'
    raw = json.loads(metadata.read_text())['config']
    cfg = ProjectConfig.model_validate(raw)
    mosaic = root/'imagery/usbos/2023'/Path(raw['imagery']['local_raster']).name
    path = mosaic.parent/(photo+'.tif')
    if path.resolve() not in source_members(mosaic):
        raise ValueError('Photo must belong to the frozen Boston acquisition')
    receipt = json.loads(path.with_suffix('.manifest.json').read_text())
    actual_hash = digest(path)
    if actual_hash != receipt['sha256']:
        raise ValueError('Original photograph hash mismatch')
    manifest = root/'chips'/cfg.dataset/'chips.parquet'
    frame = pd.read_parquet(manifest)
    frame = frame[(frame.split == 'train') & ~frame.chip_id.str.startswith('shift_')]
    entries = []
    with rasterio.open(mosaic) as parent, rasterio.open(path) as source:
        if source.count != 4 or source.res != (.3, .3) or source.crs != parent.crs:
            raise ValueError('Expected native 30 cm four-band Boston imagery')
        scale = min(1200/source.width, 1200/source.height)
        overview = Image.new('RGB', (round(source.width*scale), round(source.height*scale)), '#16221d')
        for chip in frame.itertuples():
            px, py = ~source.transform * (parent.transform*(chip.column_offset, chip.row_offset))
            x, y = round(px), round(py)
            if x < 0 or y < 0 or x+512 > source.width or y+512 > source.height:
                continue
            # Check actual source-pixel footprint, not just the parent center's split.
            if not training_footprint(source, x, y, 512, cfg):
                continue
            window = Window(x,y,512,512)
            if not (source.read_masks([1,2,3,4], window=window)>0).all():
                continue
            # Only allowed training windows are ever read/rendered. Held-out pixels stay blank.
            data = source.read([1,2,3,4], window=window)
            import io
            thumb = Image.open(io.BytesIO(png(data)))
            left, top = round(x*scale), round(y*scale)
            right, bottom = round((x+512)*scale), round((y+512)*scale)
            overview.paste(thumb.resize((right-left,bottom-top)), (left,top))
            entries.append(dict(id=chip.chip_id, x=x,y=y, size=512,
                                frozen_targets=int(chip.tree_count), split='train'))
        if not entries:
            raise ValueError('No guarded training windows in selected photo')
        destination.mkdir(parents=True, exist_ok=True)
        overview.save(destination/'overview.png')
        stat=path.stat()
        result=dict(schema_version=1,city='usbos',source_id=photo, source_sha256=actual_hash,
                    acquisition=receipt['acquisition_datetime'], raster=str(path.resolve()),
                    raster_stat=[stat.st_size,stat.st_mtime_ns], **grid(source),
                    overview_width=overview.width,overview_height=overview.height,
                    windows=entries, parent_run=run, manifest_sha256=digest(manifest),
                    split_policy=dict(seed=cfg.seed, split=cfg.split.model_dump(mode='json')),
                    training_ready=False, component_sizes=list(SIZES),
                    policy='Geometry drafts only. Guarded TRAIN footprints; other pixels hidden. No labels or training export. RGB+NIR preserved; no rescaling.')
        _write_json_atomic(destination/'source.json',result)
    print(json.dumps(dict(source=photo,windows=len(entries),workspace=str(destination))))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run',required=True)
    parser.add_argument('--photo',required=True)
    args=parser.parse_args()
    prepare(args.root,args.run,args.photo)
