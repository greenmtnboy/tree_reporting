from pathlib import Path
import json

import pytest

from urban_tree_ml.config import load_config
from urban_tree_ml.training_budget import training_budget, validate_resume_budget


def config():
    return load_config(Path(__file__).parents[1]/'configs/sf_naip_smoke.yaml')


def test_v9_budget_is_independent_of_augmented_views():
    training = config().training.model_copy(update={
        'batch_size': 2, 'accumulate_grad_batches': 16, 'epochs': 80})
    single = training_budget(training, 9045, 9045)
    paired = training_budget(training, 9045, 18090)
    assert single['trainer'] == paired['trainer'] == {
        'max_epochs': -1, 'max_steps': 22640,
        'val_check_interval': 4523, 'check_val_every_n_epoch': None}
    assert paired['optimizer_steps_per_source_pass'] == 283
    assert paired['scheduler_interval'] == 'step'
    assert paired['scheduler_t_max'] == 22640
    assert paired['patience_validation_checks'] == 12


def test_legacy_reproduction_and_resume_guard():
    training = config().training
    current = training_budget(training, 5, 10)
    with pytest.raises(ValueError, match='Legacy checkpoint'):
        validate_resume_budget({}, current)
    validate_resume_budget({'training_budget': current}, current)
    with pytest.raises(ValueError, match='differs'):
        validate_resume_budget({'training_budget': dict(current, scheduler_t_max=1)}, current)
    training.budget_policy = 'legacy_epochs'
    legacy = training_budget(training, 5, 10)
    assert legacy['trainer'] == {'max_epochs': training.epochs}
    assert legacy['scheduler_interval'] == 'epoch'
    validate_resume_budget({}, legacy)


@pytest.mark.parametrize('windows,presentations', [(0, 0), (3, 2)])
def test_reject_empty_or_inconsistent_budget(windows, presentations):
    with pytest.raises(ValueError):
        training_budget(config().training, windows, presentations)


@pytest.mark.parametrize('paired', [False, True])
@pytest.mark.parametrize('cap', [False, True, 'partial-batch'])
def test_real_trainer_stops_at_same_work_and_saves_final_state(tmp_path, monkeypatch, paired, cap):
    torch = pytest.importorskip('torch')
    pytest.importorskip('lightning')
    import pandas as pd
    from urban_tree_ml import dataset, losses, model, training

    class TinyDataset:
        def __init__(self, path, split, **kwargs):
            source_windows = 5 if cap == 'partial-batch' else 8
            self.rows = pd.DataFrame({'path': ['unused'] * (source_windows if split == 'train' else 2)})
            self.fixed_pair = split == 'train' and kwargs.get('augmentation') == 'fixed_pair_v1'
        def __len__(self):
            return len(self.rows) * (2 if self.fixed_pair else 1)
        def __getitem__(self, index):
            return {'image': torch.ones(4, 2, 2)}
        def augmentation_recipe(self, index):
            return {'index': index}

    class TinyNetwork(torch.nn.Module):
        def __init__(self, **kwargs):
            super().__init__()
            self.weight = torch.nn.Parameter(torch.ones(()))
        def forward(self, image):
            return image.mean() * self.weight

    monkeypatch.setattr(dataset, 'NpzChipDataset', TinyDataset)
    monkeypatch.setattr(model, 'RawImageryTreeModel', TinyNetwork)
    # Constant validation loss, differentiable training path: best at first check.
    monkeypatch.setattr(losses, 'multitask_loss', lambda output, batch, **kwargs: {'loss': output * 0 + 1})
    monkeypatch.setattr(training, '_git_metadata', lambda: {})
    cfg = config()
    cfg.paths.root = tmp_path
    cfg.model.crown_head = False
    directory = tmp_path/'chips'/cfg.dataset
    directory.mkdir(parents=True)
    pd.DataFrame({'city':['ussfo']}).to_parquet(directory/'chips.parquet')
    taxonomy = tmp_path/'taxonomy.json'
    taxonomy.write_text(json.dumps({'genera': ['a'], 'species': ['a']}))
    monkeypatch.setattr(training, 'taxonomy_path', lambda config: taxonomy)
    cfg.training = cfg.training.model_copy(update={
        'accelerator': 'cpu', 'precision': '32-true', 'workers': 0,
        'batch_size': 2, 'accumulate_grad_batches': 2, 'epochs': 2 if cap else 20,
        'early_stopping_patience_checks': 50 if cap else 2,
        'random_dihedral': False, 'augmentation': 'fixed_pair_v1' if paired else 'legacy'})
    result = training.run_training(cfg)
    last = torch.load(result['last_checkpoint'], map_location='cpu', weights_only=False)
    best = torch.load(result['best_checkpoint'], map_location='cpu', weights_only=False)
    assert last['global_step'] == result['global_step'] == (4 if cap else 6)
    assert best['global_step'] == (1 if paired and cap == 'partial-batch' else 2)
    assert last['training_budget'] == result['training_budget']
    assert last['lr_schedulers'][0]['last_epoch'] == result['global_step']
    assert last['lr_schedulers'][0]['T_max'] == (4 if cap else 40)
    assert result['training_budget']['trainer']['check_val_every_n_epoch'] is None
    checkpoints = list((Path(result['run_dir'])/'checkpoints').glob('*.ckpt'))
    assert len(checkpoints) >= 3  # includes distinct intra-epoch validation saves
    if cap:
        assert last['optimizer_states'][0]['param_groups'][0]['lr'] == pytest.approx(0)
