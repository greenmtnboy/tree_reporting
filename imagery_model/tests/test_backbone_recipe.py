from pathlib import Path
import pytest
from urban_tree_ml.config import load_config, ProjectConfig
from urban_tree_ml.backbone_experiment import clean_config, BACKBONES


@pytest.mark.parametrize('backbone',BACKBONES)
def test_clean_recipe_preserves_data_and_scoring(backbone):
    config=load_config(Path('configs/sf_boston_vocab_v2_sf.yaml'))
    original=config.model_dump()
    candidate=clean_config(config,'backbone-test',backbone)
    restored=ProjectConfig.model_validate_json(candidate.model_dump_json())
    assert restored.model.backbone==backbone
    assert restored.model.pretrained
    assert restored.training.batch_size*restored.training.accumulate_grad_batches==32
    assert restored.targets==config.targets
    assert restored.evaluation==config.evaluation
    assert restored.dataset==config.dataset
    assert restored.reference==config.reference
    assert config.model_dump()==original


def test_unknown_backbone_rejected():
    config=load_config(Path('configs/sf_boston_vocab_v2_sf.yaml')).model_dump()
    config['model']['backbone']='unknown'
    with pytest.raises(ValueError):ProjectConfig.model_validate(config)
