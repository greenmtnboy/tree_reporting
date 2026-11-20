"""Architecture and memory-budget contract for weights-only continuation."""
from urban_tree_ml.config import ModelConfig

LOSS_WEIGHTS = ('center_loss_weight', 'dbh_loss_weight', 'crown_loss_weight',
                'single_tree_loss_weight', 'genus_loss_weight', 'species_loss_weight',
                'confirmed_background_loss_weight')


def loss_policy_weights(policy):
    """Validate explicit future-run overrides; never infer weights for old runs."""
    import math
    weights = policy['weights']
    if not weights or set(weights) - set(LOSS_WEIGHTS):
        raise ValueError('Unknown or empty loss policy weights')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or
           not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError('Loss weights must be finite nonnegative numbers')
    return dict(weights)


def validate_warm_taxonomy(parent, frozen, *, vegetation_groups=False):
    """Accept exact continuation or the explicit append-only group expansion."""
    if vegetation_groups:
        from urban_tree_ml.vegetation_groups import expanded_taxonomy
        candidate = expanded_taxonomy(frozen)
        if parent not in (frozen, candidate):
            raise ValueError('Warm-start vocabulary mismatch')
    elif parent != frozen:
        raise ValueError('Warm-start vocabulary mismatch')


def validate_parent_cohorts(configs):
    identities = [(config.inventory.city.lower(), config.imagery.acquisition_id) for config in configs]
    cities = {city for city, _ in identities}
    if (len(identities) != len(set(identities))
            or cities not in ({'ussfo', 'usbos'}, {'ussfo', 'usbos', 'usnyc'},
                              {'ussfo', 'usbos', 'usnyc', 'usffx'})
            or any(acquisition and not acquisition.startswith(city+'-') for city, acquisition in identities)):
        raise ValueError('Warm-start requires unique city/acquisition cohorts for SF/Boston, with optional NYC/Fairfax')


def recipe_from_configs(configs):
    recipes = [dict(model=c.model.model_dump(mode='json'),
                    **{name: getattr(c.training, name) for name in LOSS_WEIGHTS},
                    batch_size=c.training.batch_size,
                    accumulate_grad_batches=c.training.accumulate_grad_batches,
                    grid=dict(resolution_m=c.imagery.resolution_m, chip_pixels=c.imagery.chip_pixels,
                              gaussian_sigma_px=c.targets.gaussian_sigma_px, nms_kernel=c.evaluation.nms_kernel))
               for c in configs]
    if not recipes or any(r != recipes[0] for r in recipes):
        raise ValueError('Warm-start parent city model/batch recipes differ')
    return recipes[0]


def validate_warm_grid(config, warm):
    expected = warm.get('grid')
    actual = dict(resolution_m=config.imagery.resolution_m, chip_pixels=config.imagery.chip_pixels,
                  gaussian_sigma_px=config.targets.gaussian_sigma_px, nms_kernel=config.evaluation.nms_kernel)
    if expected is not None and expected != actual:
        raise ValueError(f'Warm-start grid mismatch: expected {expected}, got {actual}')


def apply_warm_recipe(config, warm):
    # Old manifests intentionally retain the historical ResNet defaults.
    if 'model' in warm:
        config.model = ModelConfig.model_validate(warm['model'])
    for field in ('batch_size', 'accumulate_grad_batches', 'learning_rate', 'epochs',
                  'augmentation', 'random_dihedral', 'budget_policy', 'early_stopping_patience_checks',
                  'warmup_source_passes',
                  *LOSS_WEIGHTS):
        if field in warm:
            setattr(config.training, field, warm[field])
    config.training = type(config.training).model_validate(config.training.model_dump())
