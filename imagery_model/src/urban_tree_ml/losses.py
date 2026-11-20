from __future__ import annotations

import torch
from torch.nn import functional as functional


def masked_centernet_focal_loss(
    logits: torch.Tensor,
    target: torch.Tensor,
    valid_mask: torch.Tensor,
    confirmed_background_mask: torch.Tensor | None = None,
    confirmed_background_weight: float = 1.0,
) -> torch.Tensor:
    # bf16 rounds sigmoid/clamp to exactly 1 for ordinary positive logits.
    # Evaluate log probabilities directly in float32, avoiding log(0), including
    # at excluded pixels (inf * 0 is NaN, not a safe way to mask a loss).
    logits = logits.float()
    target = target.float()
    probability = logits.sigmoid()
    positives = target.eq(1).float()
    negatives = target.lt(1).float()
    negative_weight = (1 - target).pow(4)
    positive_loss = -functional.logsigmoid(logits) * (1 - probability).pow(2) * positives
    negative_loss = (
        -functional.logsigmoid(-logits) * probability.pow(2) * negative_weight * negatives
    )
    if confirmed_background_mask is not None:
        confirmed = confirmed_background_mask.bool() & target.eq(0)
        negative_loss = negative_loss * (1 + (confirmed_background_weight - 1) * confirmed.float())
    mask = valid_mask.float()
    normalizer = (positives * mask).sum().clamp_min(1.0)
    return ((positive_loss + negative_loss) * mask).sum() / normalizer


def single_tree_peak_loss(logits: torch.Tensor, groups: torch.Tensor) -> torch.Tensor:
    """Hard-mine extra 3x3 local peaks; backpropagate through their confidence.

    One highest peak per labeled-center partition is exempt. No confidence cutoff
    hides weak extras, and no extra penalty applies outside authoritative regions.
    Peak selection is piecewise constant, not a differentiable integer count.
    """
    values = logits.float().reshape(-1, *logits.shape[-2:])
    groups = groups.reshape_as(values)
    losses = []
    for field, ids in zip(values, groups):
        maxima = field.detach().eq(functional.max_pool2d(field.detach()[None,None], 3, stride=1, padding=1)[0,0])
        for group in torch.unique(ids):
            if group == 0:
                continue
            peaks = field[(ids == group) & maxima]
            if peaks.numel() > 1:
                extras = peaks.sort(descending=True).values[1:]
                losses.append((functional.softplus(extras)*extras.sigmoid().pow(2)).sum())
            else:
                losses.append(field.sum()*0)
    return torch.stack(losses).mean() if losses else logits.sum()*0


def multitask_loss(
    prediction: dict[str, torch.Tensor],
    batch: dict[str, torch.Tensor],
    *,
    center_weight: float,
    dbh_weight: float,
    genus_weight: float,
    species_weight: float,
    crown_weight: float = 1.0,
    single_tree_weight: float = 0.1,
    confirmed_background_weight: float = 1.0,
) -> dict[str, torch.Tensor]:
    dbh_mask = batch["dbh_mask"]
    center = masked_centernet_focal_loss(
        prediction["center_logits"], batch["center"], batch["detection_mask"],
        batch.get('confirmed_background_mask'), confirmed_background_weight,
    )

    if dbh_mask.any():
        dbh = functional.smooth_l1_loss(
            prediction["dbh_log1p"][dbh_mask], batch["dbh"][dbh_mask]
        )
    else:
        dbh = prediction["dbh_log1p"].sum() * 0

    genus_mask = batch["genus_mask"] & batch["genus"].ge(0)
    species_mask = batch["species_mask"] & batch["species"].ge(0)
    genus_logits = prediction["genus_logits"].permute(0, 2, 3, 1)
    species_logits = prediction["species_logits"].permute(0, 2, 3, 1)
    genus = (
        functional.cross_entropy(genus_logits[genus_mask], batch["genus"][genus_mask])
        if genus_mask.any()
        else genus_logits.sum() * 0
    )
    species = (
        functional.cross_entropy(species_logits[species_mask], batch["species"][species_mask])
        if species_mask.any()
        else species_logits.sum() * 0
    )
    crown = center * 0
    if "crown_log1p" in prediction:
        mask = batch["crown_mask"].bool()
        crown = ((functional.smooth_l1_loss(prediction["crown_log1p"].float()[mask], batch["crown"][mask], reduction='none') * batch['crown_mask'][mask]).mean()
                 if mask.any() else prediction["crown_log1p"].sum() * 0)
    single_tree = (single_tree_peak_loss(prediction['center_logits'], batch['single_tree_groups'])
                   if 'single_tree_groups' in batch else center*0)
    total = single_tree_weight * single_tree + crown_weight * crown + (
        center_weight * center + dbh_weight * dbh + genus_weight * genus + species_weight * species
    )
    return {
        "loss": total,
        "center_loss": center,
        "single_tree_peak_loss": single_tree,
        "dbh_loss": dbh,
        "crown_loss": crown,
        "genus_loss": genus,
        "species_loss": species,
    }
