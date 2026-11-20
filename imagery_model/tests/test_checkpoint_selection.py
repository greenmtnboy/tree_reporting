import json
from pathlib import Path

import pytest

from urban_tree_ml.checkpoint_selection import sha256, select_checkpoint, require_parent_advance


@pytest.mark.parametrize('loss,winner', [(4.1,'parent'), (4.0,'parent'), (3.9,'trained')])
def test_worse_tie_and_better(tmp_path, loss, winner):
    parent, candidate = tmp_path/'parent.ckpt', tmp_path/'candidate.ckpt'
    parent.write_bytes(b'parent'); candidate.write_bytes(b'candidate')
    baseline = dict(validation_loss=4., checkpoint_file=parent.name,checkpoint_sha256=sha256(parent),
                    parent_checkpoint='original.ckpt',parent_checkpoint_sha256='original-hash',
                    validation_inputs={'sha256':'frozen'},architecture_adapted=False)
    result = select_checkpoint(baseline,candidate,loss,tmp_path)
    assert result['selected'] == winner
    assert result['best_checkpoint'] == str(parent if winner=='parent' else candidate)
    assert result['eligible_for_parent_advance'] == (winner=='trained')
    if winner=='trained': require_parent_advance({'checkpoint_selection':result})
    else:
        with pytest.raises(ValueError,match='not eligible'): require_parent_advance({'checkpoint_selection':result})
    baseline['architecture_adapted'] = True
    assert not select_checkpoint(baseline,candidate,3.,tmp_path)['eligible_for_parent_advance']
    for bad in (float('nan'),float('inf')):
        with pytest.raises(FloatingPointError): select_checkpoint(baseline,candidate,bad,tmp_path)
    parent.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash mismatch'): select_checkpoint(baseline,candidate,loss,tmp_path)


@pytest.fixture
def tiny_training(tmp_path, monkeypatch):
    torch = pytest.importorskip('torch')
    pytest.importorskip('lightning')
    import pandas as pd
    from urban_tree_ml import dataset, losses, model, training
    from urban_tree_ml.config import load_config
    cfg = load_config(Path(__file__).parents[1]/'configs/sf_naip_smoke.yaml')
    cfg.paths.root = tmp_path
    cfg.model.crown_head = False
    cfg.training = cfg.training.model_copy(update={
        'accelerator':'cpu','precision':'32-true','workers':0,'batch_size':2,
        'accumulate_grad_batches':2,'epochs':10,'early_stopping_patience_checks':2,
        'learning_rate':.1,'weight_decay':0.,'random_dihedral':False,'augmentation':'fixed_pair_v1'})
    taxonomy = tmp_path/'taxonomy.json'
    taxonomy.write_text(json.dumps({'genera':['a'],'species':['a']}))
    directory = tmp_path/'chips'/cfg.dataset; directory.mkdir(parents=True)
    rows = []
    for split,n in [('train',8),('validation',3),('test',1)]:
        for i in range(n):
            name = f'{split}-{i}.npz'
            rows.append(dict(path=name,split=split,chip_id=f'{split}-{i}'))
            if split!='test': (directory/name).write_bytes(b'frozen label and mask fixture')
    pd.DataFrame(rows).to_parquet(directory/'chips.parquet')
    (directory/'normalization.json').write_text('{}')

    class TinyDataset:
        def __init__(self,path,split,**kwargs):
            self.root = Path(path).parent
            frame = pd.read_parquet(path)
            self.rows = frame[frame.split==split].reset_index(drop=True)
            self.fixed_pair = split=='train' and kwargs.get('augmentation')=='fixed_pair_v1'
            self.split = split
        def __len__(self): return len(self.rows)*(2 if self.fixed_pair else 1)
        def __getitem__(self,index):
            return {'image':torch.ones(4,2,2),'is_validation':self.split=='validation'}
        def augmentation_recipe(self,index): return {'index':index}

    class TinyNetwork(torch.nn.Module):
        def __init__(self,**kwargs):
            super().__init__(); self.weight = torch.nn.Parameter(torch.ones(()))
        def forward(self,image): return self.weight * image.mean()

    monkeypatch.setattr(dataset,'NpzChipDataset',TinyDataset)
    monkeypatch.setattr(model,'RawImageryTreeModel',TinyNetwork)
    monkeypatch.setattr(training,'taxonomy_path',lambda _:taxonomy)
    monkeypatch.setattr(training,'_git_metadata',lambda:{})
    parent = tmp_path/'original.ckpt'
    torch.save({'state_dict':{'network.weight':torch.tensor(1.)}},parent)
    return cfg,parent,losses,training,directory


@pytest.mark.parametrize('outcome',['worse','tie','better','nan'])
def test_real_trainer_parent_selection_and_full_zero_step_validation(tiny_training, monkeypatch, outcome):
    import torch
    cfg,parent,losses,training,directory = tiny_training
    def loss(output,batch,**kwargs):
        if batch['is_validation'][0]:
            factor = {'worse':-1.,'tie':0.,'better':1.,'nan':float('nan')}[outcome]
            return {'loss':4. + factor*(output-1.)}
        return {'loss':output}
    monkeypatch.setattr(losses,'multitask_loss',loss)
    original = parent.read_bytes()
    if outcome=='nan':
        with pytest.raises(FloatingPointError): training.run_training(cfg,initial_checkpoint=str(parent))
        assert not list((cfg.paths.root/'runs'/cfg.experiment).glob('checkpoints/*.ckpt'))
        return
    result = training.run_training(cfg,initial_checkpoint=str(parent))
    selection = result['checkpoint_selection']
    baseline = json.loads((Path(result['run_dir'])/'parent-baseline.json').read_text())
    assert baseline['validation_inputs']['windows'] == 3  # partial last batch included
    assert baseline['global_step'] == 0 and baseline['validation_loss'] == pytest.approx(4.)
    assert baseline['parent_checkpoint_sha256'] == sha256(parent)
    assert parent.read_bytes() == original
    assert not (directory/'test-0.npz').exists()  # no sealed test payload was opened
    best = torch.load(result['best_checkpoint'],map_location='cpu',weights_only=False)
    final = torch.load(result['last_checkpoint'],map_location='cpu',weights_only=False)
    assert final['global_step'] == result['global_step']
    assert final['parent_baseline'] == baseline
    assert final['state_dict']['network.weight'] < 1.
    if outcome in ('worse','tie'):
        assert selection['selected']=='parent' and not selection['eligible_for_parent_advance']
        assert best['state_dict']['network.weight'] == 1.
        assert best['global_step']==0 and best['baseline_only']
        assert result['global_step']==4  # 2 non-improving source checks, NOT first+2
        assert result['best_trained_checkpoint'] != result['best_checkpoint']
    else:
        assert selection['selected']=='trained' and selection['eligible_for_parent_advance']
        assert selection['trained_validation_loss']<4.
        assert best['state_dict']['network.weight']<1.
    # Resume must retain the control, rather than declaring a worse saved state the baseline.
    if outcome=='worse':
        with pytest.raises(ValueError,match='weights-only'):
            training.run_training(cfg,resume=result['best_checkpoint'])
        resumed = training.run_training(cfg,resume=result['last_checkpoint'])
        assert resumed['checkpoint_selection']['selected']=='parent'
        assert resumed['checkpoint_selection']['baseline_validation_loss']==4.
        (directory/'validation-0.npz').write_bytes(b'changed mask')
        with pytest.raises(ValueError,match='validation inputs'):
            training.run_training(cfg,resume=resumed['last_checkpoint'])


def test_changing_validation_after_baseline_fails_closed(tiny_training, monkeypatch):
    cfg,parent,losses,training,directory = tiny_training
    def loss(output,batch,**kwargs):
        if not batch['is_validation'][0]:
            (directory/'validation-0.npz').write_bytes(b'mutated target')
        return {'loss':output*0+4.}
    monkeypatch.setattr(losses,'multitask_loss',loss)
    with pytest.raises(ValueError,match='Validation inputs changed'):
        training.run_training(cfg,initial_checkpoint=str(parent))
    assert not (cfg.paths.root/'runs'/cfg.experiment/'checkpoint-selection.json').exists()


def test_unchanged_taxonomy_recipe_can_advance(tiny_training, monkeypatch):
    cfg, parent, losses, training, directory = tiny_training
    from urban_tree_ml import vegetation_groups
    monkeypatch.setattr(vegetation_groups, 'migrate_group_head',
                        lambda saved, initialized, parent, candidate: saved)
    monkeypatch.setattr(losses, 'multitask_loss',
                        lambda output, batch, **kw: {'loss': output})
    result = training.run_training(cfg, initial_checkpoint=str(parent),
                                  parent_taxonomy={'genera': ['a'], 'species': ['a']})
    assert not result['checkpoint_selection']['architecture_adapted']
    assert result['checkpoint_selection']['eligible_for_parent_advance']


def test_parent_baseline_and_early_stop_use_only_more_done_validation(tiny_training, monkeypatch):
    import pandas as pd
    from urban_tree_ml.curated_validation import freeze_policy, restrict_sources, POLICY
    cfg,parent,losses,training,directory = tiny_training
    inputs=cfg.paths.root/'run-inputs'/cfg.experiment
    city=inputs/'ussfo';city.mkdir(parents=True)
    (city/'manifest.json').write_text(json.dumps({'scenes':[
        {'scene_id':str(i),'validation_chip_id':f'validation-{i}','splits':['validation']} for i in range(3)]}))
    (city/'reviews.json').write_text(json.dumps({'scene_reviews':{
        '0':{'done':True},'1':{'done':True,'more_done':True},'2':{'done':True,'more_done':True}}}))
    freeze_policy(inputs,['ussfo'])
    frame=pd.read_parquet(directory/'chips.parquet')
    frame['city']='ussfo';frame['source_chip_id']=frame.chip_id
    frame.to_parquet(directory/'chips.parquet',index=False)
    frame[['chip_id','split']].to_parquet(directory/'labels.parquet',index=False)
    run=cfg.paths.root/'runs'/cfg.experiment;run.mkdir(parents=True)
    restrict_sources(inputs,{'ussfo':directory},run)
    # Unreviewed validation must not be opened even by the baseline signature.
    (directory/'validation-0.npz').unlink()
    def loss(output,batch,**kwargs):
        return {'loss':output*0+4. if batch['is_validation'][0] else output}
    monkeypatch.setattr(losses,'multitask_loss',loss)
    result=training.run_training(cfg,initial_checkpoint=str(parent))
    baseline=json.loads((run/'parent-baseline.json').read_text())
    assert baseline['validation_inputs']['windows']==2
    assert baseline['validation_inputs']['admission_policy']==POLICY
    assert baseline['validation_loss']==pytest.approx(4.)
    assert result['checkpoint_selection']['selected']=='parent'
    assert result['global_step']==4  # same patience/budget, smaller validation only


def test_real_base_start_uses_curated_baseline_and_preserves_final_state(tiny_training, monkeypatch):
    import time
    import pandas as pd
    from urban_tree_ml.curated_validation import freeze_policy, restrict_sources, POLICY
    cfg,parent,losses,training,directory = tiny_training
    cfg.model.backbone = 'swin_small'
    cfg.model.pretrained = True
    cfg.training.warmup_source_passes = .5
    inputs = cfg.paths.root/'run-inputs'/cfg.experiment
    city = inputs/'ussfo'; city.mkdir(parents=True)
    (city/'manifest.json').write_text(json.dumps({'scenes':[
        {'scene_id':str(i),'validation_chip_id':f'validation-{i}','splits':['validation']} for i in range(3)]}))
    (city/'reviews.json').write_text(json.dumps({'scene_reviews':{
        '1':{'more_done':True},'2':{'more_done':True}}}))
    freeze_policy(inputs,['ussfo'])
    frame = pd.read_parquet(directory/'chips.parquet')
    frame['city']='ussfo'; frame['source_chip_id']=frame.chip_id
    frame.to_parquet(directory/'chips.parquet',index=False)
    frame[['chip_id','split']].to_parquet(directory/'labels.parquet',index=False)
    run = cfg.paths.root/'runs'/cfg.experiment; run.mkdir(parents=True)
    restrict_sources(inputs,{'ussfo':directory},run)
    (directory/'validation-0.npz').unlink()
    (inputs/'base-start.json').write_text(json.dumps(dict(
        mode='imagenet_fresh_heads_fresh_optimizer_v1', model=cfg.model.model_dump(mode='json'),
        validation_policy=POLICY)))
    monkeypatch.setenv('TREE_ML_TRAIN_UNTIL',str(time.time()+60))
    monkeypatch.setattr(losses,'multitask_loss',lambda output,batch,**kw:{'loss':output*0+4. if batch['is_validation'][0] else output})
    result = training.run_training(cfg)
    assert result['initialization']=='imagenet_fresh_heads_fresh_optimizer_v1'
    assert not result['checkpoint_selection']['eligible_for_parent_advance']
    assert result['stop_reason']=='early_stopping'
    baseline=json.loads((run/'initialization-baseline.json').read_text())
    assert baseline['validation_inputs']['windows']==2
    assert baseline['tree_checkpoint_loaded'] is False
    assert (run/'learning-curves.jsonl').exists()
    import torch
    final=torch.load(result['last_checkpoint'],map_location='cpu',weights_only=False)
    assert final['global_step']==result['global_step']


def test_parent_winner_is_used_for_all_six_cohorts(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from urban_tree_ml.config import load_config
    from urban_tree_ml.joint import train_and_evaluate

    configs = []
    for city in ('ussfo', 'usbos', 'usnyc'):
        config = load_config(Path(__file__).parents[1]/'configs/sf_boston_vocab_v2_sf.yaml')
        config.paths.root = tmp_path
        config.inventory.city = city
        configs.append(config)
    selected = str(tmp_path/'checkpoints/parent-baseline.ckpt')
    result = {'best_checkpoint': selected, 'best_trained_checkpoint': 'worse.ckpt',
              'checkpoint_selection': {'selected': 'parent', 'eligible_for_parent_advance': False}}
    calls = []
    monkeypatch.setitem(sys.modules, 'urban_tree_ml.training',
                        SimpleNamespace(run_training=lambda *a, **kw: result))
    monkeypatch.setitem(sys.modules, 'urban_tree_ml.evaluation',
                        SimpleNamespace(run_evaluation=lambda *a, **kw: calls.append(kw)))
    train_and_evaluate(configs, 'frozen', tmp_path)
    assert len(calls) == 6
    assert all(call['checkpoint_path'] == selected for call in calls)
    assert {call['cohort'] for call in calls} == {
        'validation', 'validation-usbos', 'validation-usnyc',
        'train-ussfo', 'train-usbos', 'train-usnyc'}
    assert json.loads((tmp_path/'training-result.json').read_text()) == result
    assert (tmp_path/'COMPLETE').exists()
    with pytest.raises(ValueError, match='not eligible'):
        require_parent_advance(result)


def test_warm_start_enforces_deadline_and_preserves_selection(tiny_training, monkeypatch):
    from types import SimpleNamespace
    from urban_tree_ml import capacity_run
    import torch
    cfg,parent,losses,training,directory=tiny_training
    clock={'at':100.,'batches':0}
    monkeypatch.setenv('TREE_ML_TRAIN_UNTIL','200')
    monkeypatch.setattr(capacity_run,'time',SimpleNamespace(time=lambda:clock['at']))
    def loss(output,batch,**kwargs):
        if not batch['is_validation'][0]:
            clock['batches']+=1
            # A source check occurs after four batches. Expire after one complete
            # validation, so the saved candidate and true final state can differ.
            if clock['batches']==6:clock['at']=201.
        return {'loss':output}
    monkeypatch.setattr(losses,'multitask_loss',loss)
    result=training.run_training(cfg,initial_checkpoint=str(parent))
    assert result['stop_reason']=='wall_clock_training_cutoff'
    assert result['training_cutoff_epoch']==200.
    assert clock['batches']==6
    assert result['checkpoint_selection']['selected']=='trained'
    last=torch.load(result['last_checkpoint'],map_location='cpu',weights_only=False)
    assert last['global_step']==result['global_step']==3
    assert (cfg.paths.root/'runs'/cfg.experiment/'learning-curves.jsonl').exists()


def test_expired_warm_start_deadline_refuses_optimization(tiny_training, monkeypatch):
    cfg,parent,_,training,_=tiny_training
    monkeypatch.setenv('TREE_ML_TRAIN_UNTIL','1')
    with pytest.raises(ValueError,match='expired training cutoff'):
        training.run_training(cfg,initial_checkpoint=str(parent))
    assert not (cfg.paths.root/'runs'/cfg.experiment/'checkpoints').exists()
