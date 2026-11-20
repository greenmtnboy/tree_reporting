"""Build a species-first atlas from the locked Linux v20 validation reconstruction."""
import argparse
import base64
import ctypes
import hashlib
import io
import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from PIL import Image
from urban_tree_ml.config import load_config
from urban_tree_ml.model import RawImageryTreeModel
from urban_tree_ml.species_microscope import inspect_species_centers, species_summary

RUN = 'sf-boston-nyc-fairfax-naip-swin-30cm-v20-curated-finetune'
EXPECTED_SHA = 'ab428f589573be7666f840d467f672ad3144b50a948a8b621cc6eb0ceec86c9d'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2); torch.set_num_interop_threads(2)
    if os.name == 'nt':
        k = ctypes.windll.kernel32
        k.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        assert k.SetProcessAffinityMask(ctypes.c_void_p(-1), 3)
        assert k.SetPriorityClass(ctypes.c_void_p(-1), 0x4000)
    root, out = args.root, args.output
    out.mkdir(parents=True, exist_ok=False)
    run = root/'runs'/RUN
    assert (run/'COMPLETE').exists()
    checkpoint = run/'checkpoints/001-0000569.ckpt'
    assert sha(checkpoint) == EXPECTED_SHA
    rebuilt = root/'benchmarks/v20-linux-reproduction/output/occupancy'
    assert (rebuilt/'COMPLETE').exists()
    audit = rebuilt.parent/'both-models-mask-audit.json'
    assert all(r['labels_equal'] and r['maximum_mask_difference'] == 0
               for r in json.loads(audit.read_text())['rows'])
    cfg = load_config(run/'config-ussfo.json')
    taxonomy = json.loads((run/'vegetation-taxonomy.json').read_text())
    names = taxonomy['species']
    payload = torch.load(checkpoint, map_location='cpu', weights_only=False)
    model = RawImageryTreeModel(input_channels=cfg.model.input_channels,
        feature_channels=cfg.model.feature_channels, genus_classes=len(taxonomy['genera']),
        species_classes=len(names), backbone=cfg.model.backbone,
        crown_head=cfg.model.crown_head, pretrained=False).eval()
    model.load_state_dict({k.removeprefix('network.'): v for k,v in payload['state_dict'].items()
                          if k.startswith('network.')}, strict=True)
    del payload
    rows, tiles, files, counts = [], {}, {}, {}
    errors = []; start = time.perf_counter()
    for city, expected in [('ussfo',220),('usbos',89),('usnyc',10),('usffx',2)]:
        directory = rebuilt/'chips'/(city+'-groups')
        manifest = pd.read_parquet(directory/'chips.parquet')
        assert len(manifest) == expected and set(manifest.split) == {'validation'}
        assert manifest.chip_id.is_unique
        normpath = root/'chips'/(RUN+'-'+city)/'normalization.json'
        norm = json.loads(normpath.read_text())
        mean = torch.tensor(norm['mean'])[:,None,None]
        std = torch.tensor(norm['std'])[:,None,None]
        files[str(normpath)] = sha(normpath)
        files[str(directory/'chips.parquet')] = sha(directory/'chips.parquet')
        counts[city] = dict(cohort_tiles=expected, labeled_tiles=0, species_centers=0)
        for number, row in enumerate(manifest.itertuples()):
            path = directory/row.path
            files[str(path)] = sha(path)
            with np.load(path) as a:
                mask = (a['species_mask'] > 0) & (a['species'] >= 0)
                y,x = np.where(mask)
                if not len(y):
                    continue
                truth = a['species'][y,x].astype(int)
                assert (truth < len(names)).all()
                raw = a['image'].copy()
            inspected = inspect_species_centers(model, (torch.from_numpy(raw).float()-mean)[None]/std, y,x)
            assert inspected['features'].min() >= 0
            probs = inspected['logits'].softmax(-1)
            top5 = probs.topk(min(5,len(names)),dim=-1).indices.tolist()
            conf, pred = probs.max(-1)
            rgb = Image.fromarray(np.rint(np.clip(raw[:3].transpose(1,2,0),0,1)*255).astype('uint8'))
            stream=io.BytesIO(); rgb.save(stream,format='JPEG',quality=85)
            key=city+':'+row.chip_id
            tiles[key]=dict(city=city,chip=row.chip_id,image='data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode())
            for i, label in enumerate(truth):
                rows.append(dict(id=f'{key}:{x[i]}:{y[i]}',city=city,chip=row.chip_id,tile=key,
                    x=int(x[i]*cfg.targets.output_stride), y=int(y[i]*cfg.targets.output_stride),
                    truth=int(label),predicted=int(pred[i]),confidence=float(conf[i]),top5=top5[i],
                    features=inspected['features'][i].tolist()))
            errors.append([inspected['reconstruction_error'],inspected['zeroing_error']])
            counts[city]['labeled_tiles']+=1;counts[city]['species_centers']+=len(y)
            if number%10==0 or number==expected-1:
                print(city,number+1,'/',expected,'centers',len(rows),'seconds',round(time.perf_counter()-start),flush=True)
        print('CITY COMPLETE',city,counts[city],flush=True)
    atlas=dict(schema_version=1,run=RUN,checkpoint=checkpoint.name,checkpoint_sha256=EXPECTED_SHA,
        basis='Classification at supervised labeled centers; independent of detection',
        split='validation',cohort_counts=counts,names=names,rows=rows,tiles=tiles,
        weights=inspected['weights'].tolist(),bias=inspected['bias'].tolist(),
        summary=species_summary(rows,names),precision='CPU float32',
        files=files,mask_audit_sha256=sha(audit),
        reconstruction_max_error=max(e[0] for e in errors),zeroing_max_error=max(e[1] for e in errors),
        elapsed_seconds=time.perf_counter()-start,
        source_sha256={str(p):sha(p) for p in [Path(__file__),Path(__file__).parents[1]/'src/urban_tree_ml/species_microscope.py']})
    (out/'atlas.json').write_text(json.dumps(atlas,separators=(',',':')),encoding='utf-8')
    (out/'COMPLETE').write_text('Frozen validation species-center diagnostic complete; no training or annotation changes.\n')
    print('COMPLETE',len(rows),'species centers',len(tiles),'labeled tiles',flush=True)


if __name__ == '__main__':
    main()
