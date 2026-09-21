"""Architecture and memory-budget contract for weights-only continuation."""
from urban_tree_ml.config import ModelConfig


def recipe_from_configs(configs):
    recipes = [dict(model=c.model.model_dump(mode='json'),
                    batch_size=c.training.batch_size,
                    accumulate_grad_batches=c.training.accumulate_grad_batches)
               for c in configs]
    if not recipes or any(r != recipes[0] for r in recipes):
        raise ValueError('Warm-start parent city model/batch recipes differ')
    return recipes[0]


def apply_warm_recipe(config, warm):
    # Old manifests intentionally retain the historical ResNet defaults.
    if 'model' in warm:
        config.model = ModelConfig.model_validate(warm['model'])
    for field in ('batch_size', 'accumulate_grad_batches', 'learning_rate', 'epochs'):
        if field in warm:
            setattr(config.training, field, warm[field])
    config.training = type(config.training).model_validate(config.training.model_dump())
