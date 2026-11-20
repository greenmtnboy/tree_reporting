"""Bounded CPU pilot: native channels behind Logan detections, read-only inputs."""
import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import pandas as pd
import torch
from PIL import Image
from pyproj import Transformer
from shapely.geometry import Point, Polygon
from shapely.ops import transform

from urban_tree_ml.config import load_config
from urban_tree_ml.dataset import NpzChipDataset
from urban_tree_ml.interpretability import inspect_center_head, sample_points
from urban_tree_ml.model import RawImageryTreeModel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    torch.set_num_interop_threads(2)
    if os.name == 'nt':
        k = ctypes.windll.kernel32
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        assert k.SetProcessAffinityMask(ctypes.c_void_p(-1), 3)
        assert k.SetPriorityClass(ctypes.c_void_p(-1), 0x4000)
    root, run, output = args.root, args.run, args.output
    output.mkdir(parents=True, exist_ok=False)
    parent = root/'runs'/run
    assert (parent/'COMPLETE').exists()
    promotion = json.loads((parent/'user-promotion.json').read_text())
    checkpoint = parent/'checkpoints'/promotion['checkpoint']
    assert sha(checkpoint) == promotion['checkpoint_sha256']
    acquisition = 'usbos-naip-2025'
    prepared = root/'run-inputs'/(run+'-'+acquisition+'-predictions')
    cfg = load_config(prepared/'config.json')
    chips = root/'chips'/cfg.dataset
    dataset = NpzChipDataset(chips/'chips.parquet', 'train')
    available = {str(row.chip_id):i for i, row in dataset.rows.iterrows()}
    review_dir = root/'qa/registration'/acquisition
    snapshots = {}
    for name in ['manifest.json', 'reviews.json']:
        raw = (review_dir/name).read_bytes()
        (output/name).write_bytes(raw)
        snapshots[name] = hashlib.sha256(raw).hexdigest()
    manifest = json.loads((output/'manifest.json').read_text())
    state = json.loads((output/'reviews.json').read_text())
    scene_by_chip = {s['validation_chip_id']:s for s in manifest['scenes'] if s.get('validation_chip_id')}
    queue_path = root/'benchmarks/zz-logan-runways-citywide-v13-review-20261106/validation-targets.csv'
    queue = pd.read_csv(queue_path)
    logan = sorted(set(queue.loc[queue.city=='usbos', 'chip_id']) & available.keys())
    assert logan and len(logan) <= 24
    comparisons = sorted((chip for chip in available if chip not in logan and chip in scene_by_chip
        and state['scene_reviews'].get(scene_by_chip[chip]['scene_id'],{}).get('more_done') is True
        and scene_by_chip[chip].get('tree_count',0) >= 10),
        key=lambda chip:(-scene_by_chip[chip]['tree_count'],chip))[:6]
    selected = logan + comparisons
    assert len(selected) <= 30
    taxonomy = json.loads((parent/'vegetation-taxonomy.json').read_text())
    payload = torch.load(checkpoint, map_location='cpu', weights_only=False)
    model = RawImageryTreeModel(input_channels=cfg.model.input_channels,
        feature_channels=cfg.model.feature_channels, genus_classes=len(taxonomy['genera']),
        species_classes=len(taxonomy['species']), backbone=cfg.model.backbone,
        crown_head=cfg.model.crown_head, pretrained=False).eval()
    model.load_state_dict({k.removeprefix('network.'):v for k,v in payload['state_dict'].items()
                           if k.startswith('network.')}, strict=True)
    del payload
    import rasterio
    with rasterio.open(prepared/'imagery-30cm.vrt') as raster:
        affine, crs = raster.transform, raster.crs
    project = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    backgrounds = [transform(project.transform, Polygon(r['polygon'])) for r in state['mask_regions']
                   if r['mode']=='confirmed-background' and r.get('polygon')]
    backgrounds = [p if p.is_valid else __import__('shapely').make_valid(p) for p in backgrounds]
    tiles, pooled_maps, maxima = [], [], []
    for chip in selected:
        index = available[chip]
        row = dataset.rows.iloc[index]
        path = chips/row.path
        batch = dataset[index]['image'][None]
        inspection = inspect_center_head(model, batch)
        activations = inspection['features'][0]
        contributions = inspection['contributions'][0]
        logits = inspection['logits'][0]
        assert activations.min() >= 0  # This head uses post-ReLU channels.
        points = sample_points(logits, cfg.evaluation.nms_kernel, 24)
        stride = cfg.targets.output_stride
        point_rows = []
        for y, x in points.tolist():
            wx, wy = affine*(int(row.column_offset)+x*stride, int(row.row_offset)+y*stride)
            point_rows.append(dict(x=x*stride,y=y*stride,logit=float(logits[y,x]),
                contributions=contributions[:,y,x].tolist(),
                in_confirmed_background=any(p.covers(Point(wx,wy)) for p in backgrounds)))
        with np.load(path) as packed:
            rgb = np.rint(np.clip(packed['image'][:3].transpose(1,2,0),0,1)*255).astype(np.uint8)
        stream=io.BytesIO();Image.fromarray(rgb).save(stream,format='PNG')
        peak_values, peak_locations = activations.flatten(1).max(1)
        maxima.append(peak_values.numpy())
        pooled_maps.append(torch.nn.functional.adaptive_avg_pool2d(activations, (32,32)).numpy())
        scene = scene_by_chip[chip]
        tiles.append(dict(chip=chip, acquisition=acquisition, city='usbos',split='train',
            group='Logan runway review' if chip in logan else 'Curated comparison tile',
            image='data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),
            width=rgb.shape[1],height=rgb.shape[0], points=point_rows,
            channel_maxima=peak_values.tolist(),
            channel_peak_xy=[[(int(i)%logits.shape[1])*stride,(int(i)//logits.shape[1])*stride] for i in peak_locations],
            review_url='http://127.0.0.1:8765/registration?'+urlencode(dict(city='usbos',acquisition=acquisition,
                scene=scene['scene_id'],run=run+'::train-usbos-naip-2025',queue_chip=chip,fullscreen=1)),
            input_sha256=sha(path), reconstruction_max_error=inspection['reconstruction_max_error'],
            ablation_max_error=inspection['ablation_max_error'],ablation_checked_channel=inspection['ablation_checked_channel']))
        print(chip,tiles[-1]['group'],'max score',float(logits.sigmoid().max()),flush=True)
    scales=np.maximum(np.stack(maxima).max(0),1e-12)
    for tile, maps in zip(tiles,pooled_maps):
        tile['maps']=base64.b64encode(np.rint(np.clip(maps/scales[:,None,None],0,1)*255).astype(np.uint8).tobytes()).decode()
    atlas=dict(schema_version=1,run=run,checkpoint=promotion['checkpoint'],checkpoint_sha256=sha(checkpoint),
        created_at=datetime.now(timezone.utc).isoformat(),layer='center_head.0 → center_head.1',
        method='Exact final 1×1 readout contributions; post-ReLU channels, CPU float32',
        bias=inspection['bias'],weights=inspection['weights'].tolist(),scales=scales.tolist(),map_size=32,
        tiles=tiles,snapshot_sha256=snapshots,normalization_sha256=sha(chips/'normalization.json'),
        config_sha256=sha(prepared/'config.json'),queue_sha256=sha(queue_path),
        source_sha256={name:sha(Path(__file__).parents[1]/'src/urban_tree_ml'/name)
            for name in ['model.py','backbones.py','interpretability.py']},
        limitations=['Selected training tiles only; no generalization or quality estimate.',
            'Background badges describe the saved human regions; no automated truth labels.',
            'CPU float32 is a fresh replay and may differ slightly from archived GPU bf16 predictions.',
            'Channel zeroing is exact only at the final readout. It is not a retraining recommendation.',
            'Heatmaps are 32×32 mean-pooled previews; point contributions use full-resolution activations.',
            'Top examples are restricted to this small pilot, not a representative dataset search.'])
    (output/'atlas.json').write_text(json.dumps(atlas,separators=(',',':')),encoding='utf-8')
    template=Path(__file__).parents[1]/'src/urban_tree_ml/detection_microscope.html'
    (output/'index.html').write_text(template.read_text(encoding='utf-8').replace('__ATLAS__',
        json.dumps(atlas,separators=(',',':')).replace('<','\\u003c')),encoding='utf-8')
    (output/'COMPLETE').write_text('Read-only CPU pilot complete\n',encoding='utf-8')
    print('COMPLETE',output,flush=True)


if __name__ == '__main__':
    main()
