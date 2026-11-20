"""Fill covered Fairfax development review gaps with the explicitly promoted model.

Prepare/infer in an isolated dataset and staging output; publish only verified,
nonoverlapping cohorts. Does not modify curation, training inputs or old results.
"""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def main(root, run, publish):
    import numpy as np
    import pandas as pd
    import torch
    from urban_tree_ml.checkpoint_selection import resolve_parent_checkpoint
    from urban_tree_ml.config import load_config
    from urban_tree_ml.evaluation import run_evaluation
    from urban_tree_ml.fairfax_curation import RUN, development_plan
    from urban_tree_ml.mosaic_coverage import missing_chips
    from urban_tree_ml.prediction_chips import build_prediction_chips

    torch.set_num_threads(2)
    name = run+'-fairfax-review-20261115'
    work = root/'qa'/name
    model = root/'runs'/run
    checkpoint = resolve_parent_checkpoint(model)
    checkpoint_sha = sha(checkpoint)
    assert checkpoint_sha == read(model/'user-promotion.json')['checkpoint_sha256']
    if publish:
        receipt = read(work/'receipt.json')
        assert (work/'READY').is_file() and receipt['checkpoint_sha256'] == checkpoint_sha
        for file, digest in receipt['original_sha256'].items():
            assert sha(file) == digest
        destinations = [(work/'staging/evaluation'/c, model/'evaluation'/c) for c in receipt['cohorts']]
        assert all(not destination.exists() for _,destination in destinations)
        for source, destination in destinations:
            for path in source.iterdir():
                if path.is_file():
                    assert sha(path) == receipt['outputs_sha256'][str(path)]
            for file in ('metrics.json','evaluation-metadata.json','inference-coverage.json'):
                path = source/file
                value = read(path)
                value.update(run_id=run, supplemental_inference=name, checkpoint_sha256=checkpoint_sha)
                for key in ('predictions','matches','ground_truth','taxonomy','evaluation_metadata','metrics','inference_coverage'):
                    if key in value:
                        value[key] = str(destination/Path(value[key]).name)
                write(path,value)
            source.rename(destination)
        write(work/'published.json',dict(checkpoint_sha256=checkpoint_sha,cohorts=receipt['cohorts']))
        (work/'COMPLETE').write_text('Verified supplemental Fairfax predictions published.\n')
        print('PUBLISHED',receipt['counts'],flush=True)
        return

    assert not work.exists(), 'Inspect prior preparation before starting another pass'
    config = load_config(model/'config-usffx.json')
    source_config = load_config(root/'run-inputs'/RUN/'config.json')
    raster = source_config.imagery.local_raster
    plan = development_plan(root/'run-inputs'/RUN, root/'inventory/usffx/inventory.parquet')
    planned = {f"r{w['row']//512:06d}_c{w['col']//512:06d}":w for w in plan['windows']}
    assert len(planned) == len(plan['windows']) and all(w['split'] in ('train','validation') for w in planned.values())
    existing = set()
    original = {}
    for split in ('train','validation'):
        directory = model/'evaluation'/(split+'-usffx')
        coverage = read(directory/'inference-coverage.json')
        assert coverage['complete']
        for row in coverage['chips']:
            assert planned[row['chip_id']]['split'] == split
            existing.add(row['chip_id'])
        original.update({str(p):sha(p) for p in directory.iterdir() if p.is_file()})
    original.update({str(model/file):sha(model/file) for file in ('training-result.json','user-promotion.json')})
    uncovered = missing_chips(raster, 512, planned)
    wanted = {chip:w for chip,w in planned.items() if chip not in uncovered|existing}
    assert wanted
    windows = {(w['row'],w['col']):w['split'] for w in wanted.values()}
    config.paths.root = root
    config.dataset = name
    config.experiment = run
    config.imagery.local_raster = raster
    config.training.accelerator = 'cpu'
    config.training.workers = 0
    config.training.batch_size = 2
    config.evaluation.confidence_threshold = .13
    config.reference.taxonomy_path = model/'vegetation-taxonomy.json'
    config.reference.normalization_path = root/'chips'/(run+'-usffx')/'normalization.json'
    work.mkdir(parents=True)
    write(work/'plan.json', dict(planned=len(planned),existing=sorted(existing),uncovered=sorted(uncovered),
                               wanted={k:v['split'] for k,v in wanted.items()},source_raster=str(raster)))
    print('Fairfax plan:',len(wanted),'missing covered tiles;',len(uncovered),'uncovered excluded;',len(existing),'existing retained',flush=True)
    build_prediction_chips(config,raster,windows,window_splits=windows,
                           labels_template=root/'chips'/(run+'-usffx')/'labels.parquet')
    dataset=root/'chips'/name
    summary=read(dataset/'summary.json');summary['catalog_role']='supplemental_inference'
    write(dataset/'summary.json',summary)
    for path in dataset.rglob('*.npz'):
        with np.load(path) as arrays:
            assert not arrays['detection_mask'].any() and not arrays['scoring_mask'].any()
    assert pd.read_parquet(dataset/'labels.parquet').empty
    write(work/'config.json',config.model_dump(mode='json'))
    counts={split:sum(w['split']==split for w in wanted.values()) for split in ('train','validation')}
    cohorts=[]
    for split,count in counts.items():
        if not count:
            continue
        cohort=split+'-usffx-review'
        assert not (model/'evaluation'/cohort).exists()
        print('Predicting',split,count,'tiles with',checkpoint.name,flush=True)
        run_evaluation(config,checkpoint,split=split,device_name='cpu',cohort=cohort,output_run=work/'staging')
        folder=work/'staging/evaluation'/cohort
        coverage=read(folder/'inference-coverage.json')
        expected={chip for chip,w in wanted.items() if w['split']==split}
        assert coverage['complete'] and coverage['chip_count']==len(expected)
        assert {r['chip_id'] for r in coverage['chips']}==expected
        predictions=pd.read_parquet(folder/'predictions.parquet')
        assert set(predictions.chip_id)<=expected and predictions.score.between(0,1).all()
        assert predictions.detection_mask_value.eq(0).all()
        assert pd.read_parquet(folder/'ground-truth.parquet').empty
        metadata=read(folder/'evaluation-metadata.json')
        assert metadata['prediction_only'] and Path(metadata['checkpoint'])==checkpoint
        cohorts.append(cohort)
    for file,digest in original.items():
        assert sha(file)==digest
    write(work/'receipt.json',dict(checkpoint=str(checkpoint),checkpoint_sha256=checkpoint_sha,
          counts=counts,cohorts=cohorts,source_raster_sha256=sha(raster),
          config_sha256=sha(work/'config.json'),normalization_sha256=sha(dataset/'normalization.json'),
          original_sha256=original,outputs_sha256={str(p):sha(p) for p in (work/'staging/evaluation').rglob('*') if p.is_file()}))
    (work/'READY').write_text('Coverage and provenance verified; staged for publication.\n')
    print('READY',counts,flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run',required=True)
    parser.add_argument('--publish',action='store_true')
    args=parser.parse_args()
    if os.name=='nt':
        kernel=ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetProcessAffinityMask.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        assert kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(),3)
        assert kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x4000)
    main(Path(os.environ['TREE_ML_DATA_ROOT']),args.run,args.publish)
