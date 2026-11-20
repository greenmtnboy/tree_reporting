"""Audit the collected native NYC adaptation against frozen development inputs."""
import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd

RUN='nyc-naip-swin-30cm-v8-adaptation'
BASE='nyc-naip-native30-v7-baseline'
PARENT='sf-boston-naip-swin-30cm-v7-finetune'

def digest(path):
    with path.open('rb') as source:return hashlib.file_digest(source,'sha256').hexdigest()

def audit(root):
    directory=root/'runs'/RUN
    assert (directory/'COMPLETE').is_file()
    status=json.loads((root/(RUN+'-status.json')).read_text())
    assert status['instance_id']=='4b7087f26f564d8588fa1c1459a6746a' and status['exit_code']==0
    result=json.loads((directory/'training-result.json').read_text())
    checkpoint=directory/'checkpoints'/Path(result['best_checkpoint']).name
    receipt={'experiment':RUN,'supervisor':status,'training':result,
        'checkpoint_sha256':digest(checkpoint),'archive_sha256':digest(root/(RUN+'-results.tar.gz')),'cohorts':{}}
    jobs=[('validation-usnyc',19,BASE),('train-usnyc',245,BASE),
          ('train-usnyc-central-park',187,BASE),('validation',567,PARENT),('validation-usbos',637,PARENT)]
    for cohort,count,baseline in jobs:
        current=directory/'evaluation'/cohort
        previous=root/'runs'/baseline/'evaluation'/cohort
        metadata=json.loads((current/'evaluation-metadata.json').read_text())
        coverage=json.loads((current/'inference-coverage.json').read_text())
        assert coverage['complete'] and coverage['chip_count']==count
        assert Path(coverage['checkpoint']).name==checkpoint.name
        assert Path(metadata['checkpoint']).name==checkpoint.name
        ids=[v['chip_id'] for v in coverage['chips']]
        manifest=pd.read_parquet(root/'chips'/metadata['config']['dataset']/'chips.parquet')
        assert len(set(ids))==count
        assert set(ids)==set(manifest.loc[manifest.split==metadata['split'],'chip_id'])
        predictions=pd.read_parquet(current/'predictions.parquet')
        assert set(predictions.chip_id)<=set(ids)
        assert predictions.groupby('chip_id').size().to_dict()=={v['chip_id']:v['prediction_count'] for v in coverage['chips'] if v['prediction_count']}
        truth=pd.read_parquet(current/'ground-truth.parquet')
        old=pd.read_parquet(previous/'ground-truth.parquet')
        columns=sorted(truth.columns)
        assert columns==sorted(old.columns)
        sort=['chip_id','tree_id']
        pd.testing.assert_frame_equal(truth[columns].sort_values(sort).reset_index(drop=True),
            old[columns].sort_values(sort).reset_index(drop=True),check_exact=True)
        assert json.loads((current/'taxonomy.json').read_text())==json.loads((previous/'taxonomy.json').read_text())
        if cohort.endswith('central-park'):
            assert metadata['prediction_only'] and truth.empty
        receipt['cohorts'][cohort]={'chips':count,'truth_rows':len(truth),'identical_frozen_truth':True,
            'truth_sha256':digest(current/'ground-truth.parquet'),'predictions_sha256':digest(current/'predictions.parquet'),
            'species_supported_rows':int(truth.species_id.ge(0).sum()),'genus_supported_rows':int(truth.genus_id.ge(0).sum()),
            'prediction_only':metadata['prediction_only']}
    plan=json.loads((root/'run-inputs'/RUN/'split-plan.json').read_text())
    assert not plan['test_evaluated'] and not plan['test_pixels_materialized']
    receipt['test_sealed']=True
    output=root/'benchmarks/nyc-native30-v8-audit';output.mkdir(parents=True,exist_ok=True)
    (output/'verification.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,required=True)
    audit(parser.parse_args().root)
