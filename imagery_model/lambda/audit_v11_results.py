"""Offline verification of the recovered v11 result; never reads live annotations."""
import hashlib
import json
from pathlib import Path
import pandas as pd

ROOT=Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
RUN='sf-boston-nyc-naip-swin-30cm-v11-curated-finetune-retry1'
PARENT='sf-boston-nyc-naip-swin-30cm-v10-curated-finetune'
def sha(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream,'sha256').hexdigest()
def main():
    run=ROOT/'runs'/RUN
    assert (run/'COMPLETE').exists()
    status=json.loads((ROOT/(RUN+'-status.json')).read_text()); assert status['exit_code']==0
    result=json.loads((run/'training-result.json').read_text())
    checkpoint=run/'checkpoints'/Path(result['best_checkpoint']).name
    digest=sha(checkpoint); assert digest=='da99c7a2c98d74e2702931127a2be3163cd5245606e4294f67d09801cbcc31d8'
    audit={'run':RUN,'status':status,'training':result,'checkpoint_sha256':digest,
           'archive_sha256':sha(ROOT/(RUN+'-results.tar.gz')),'cohorts':{},'validation_comparison':{}}
    expected={'train-ussfo':4468,'validation':567,'train-usbos':4577,'validation-usbos':637,'train-usnyc':6,'validation-usnyc':19}
    for cohort,count in expected.items():
        d=run/'evaluation'/cohort
        coverage=json.loads((d/'inference-coverage.json').read_text())
        assert coverage['complete'] and coverage['chip_count']==count
        ids=[x['chip_id'] for x in coverage['chips']]; assert len(ids)==len(set(ids))==count
        p=pd.read_parquet(d/'predictions.parquet'); y=pd.read_parquet(d/'ground-truth.parquet')
        assert set(p.chip_id)<=set(ids) and set(y.chip_id)<=set(ids)
        assert len(p)==sum(x['prediction_count'] for x in coverage['chips'])
        metadata=json.loads((d/'evaluation-metadata.json').read_text())
        assert Path(metadata['checkpoint']).name==checkpoint.name
        taxonomy=json.loads((d/'taxonomy.json').read_text()); assert len(taxonomy['species'])==99
        audit['cohorts'][cohort]={'chips':count,'predictions':len(p),'truth_rows':len(y),
            'vegetation_targets':y.vegetation_type.value_counts().to_dict() if 'vegetation_type' in y else {}}
        if cohort.startswith('validation'):
            old=pd.read_parquet(ROOT/'runs'/PARENT/'evaluation'/cohort/'ground-truth.parquet').reset_index(drop=True)
            shared=list(old.columns.intersection(y.columns))
            same=old[shared].equals(y.reset_index(drop=True)[shared])
            audit['validation_comparison'][cohort]={'parent_rows':len(old),'candidate_rows':len(y),'common_columns_equal':same,
                'parent_only_columns':list(old.columns.difference(y.columns)),'candidate_only_columns':list(y.columns.difference(old.columns))}
            differences={}
            for col in shared:
                left,right=old[col],y.reset_index(drop=True)[col]
                if not left.equals(right):
                    changed=~(left.eq(right)|(left.isna()&right.isna()))
                    differences[col]={'rows':int(changed.sum())}
                    if pd.api.types.is_numeric_dtype(left): differences[col]['max_absolute_delta']=float((left-right).abs().max())
            audit['validation_comparison'][cohort]['differences']=differences
            scoring=[k for k in shared if k not in ('pixel_row','pixel_col')]
            audit['validation_comparison'][cohort]['scoring_columns_equal']=old[scoring].equals(y[scoring])
            if cohort=='validation-usnyc': assert same
    admission=json.loads((run/'nyc-curated-admission.json').read_text())
    assert admission['train_chips']==6 and admission['unreviewed_training_chips']==0 and admission['test_evaluated'] is False
    assert audit['cohorts']['train-usnyc']['truth_rows']==677
    audit['nyc_admission']=admission
    for city in ('ussfo','usbos','usnyc'):
        config=json.loads((run/f'config-{city}.json').read_text())
        tr=config['training']; assert (tr['learning_rate'],tr['batch_size'],tr['accumulate_grad_batches'],tr['early_stopping_patience_checks'])==(3e-5,2,16,4)
        assert (tr['augmentation'],tr['budget_policy'])==('fixed_pair_v1','source_passes_v1')
        assert (config['imagery']['chip_pixels'],config['imagery']['resolution_m'])==(512,.3)
    destination=ROOT/(RUN+'-audit.json'); destination.write_text(json.dumps(audit,indent=2)+'\n')
    print(json.dumps(audit,indent=2))
if __name__=='__main__': main()
