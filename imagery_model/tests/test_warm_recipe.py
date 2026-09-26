from pathlib import Path
import pytest
from urban_tree_ml.config import load_config
from urban_tree_ml.warm_recipe import apply_warm_recipe, recipe_from_configs


def test_swin_continuation_preserves_heads_and_batch_budget():
    base = load_config(Path('configs/sf_boston_vocab_v2_sf.yaml'))
    parent = base.model_copy(deep=True)
    parent.model.backbone = 'swin_tiny'
    parent.model.crown_head = True
    parent.training.batch_size = 8
    parent.training.accumulate_grad_batches = 4
    recipe = recipe_from_configs([parent, parent.model_copy(deep=True)])
    apply_warm_recipe(base, {**recipe, 'epochs': 80, 'learning_rate': 3e-5})
    assert base.model == parent.model
    assert base.training.batch_size * base.training.accumulate_grad_batches == 32
    assert base.training.epochs == 80
    assert base.training.learning_rate == 3e-5


def test_mismatched_parent_recipes_fail():
    base = load_config(Path('configs/sf_boston_vocab_v2_sf.yaml'))
    other = base.model_copy(deep=True)
    other.model.backbone = 'swin_tiny'
    with pytest.raises(ValueError, match='differ'):
        recipe_from_configs([base, other])


def test_legacy_manifest_still_supported():
    base = load_config(Path('configs/sf_boston_vocab_v2_sf.yaml'))
    apply_warm_recipe(base, {'epochs': 40, 'learning_rate': 3e-5})
    assert base.model.backbone == 'resnet34'
