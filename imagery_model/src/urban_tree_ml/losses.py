from __future__ import annotations

import torch
from torch.nn import functional as functional


def masked_centernet_focal_loss(
    logits: torch.Tensor,
    target: torch.Tensor,
    valid_mask: torch.Tensor,
) -> torch.Tensor:
    probability = logits.sigmoid().clamp(1e-6, 1 - 1e-6)
    positives = target.eq(1).float()
    negatives = target.lt(1).float()
    negative_weight = (1 - target).pow(4)
    positive_loss = -(probability.log()) * (1 - probability).pow(2) * positives
    negative_loss = -((1 - probability).log() * probability.pow(2) * negative_weight * negatives)
    mask = valid_mask.float()
    normalizer = (positives * mask).sum().clamp_min(1.0)
    return ((positive_loss + negative_loss) * mask).sum() / normalizer


def multitask_loss(
    prediction: dict[str, torch.Tensor],
    batch: dict[str, torch.Tensor],
    *,
    center_weight: float,
    dbh_weight: float,
    genus_weight: float,
    species_weight: float,
) -> dict[str, torch.Tensor]:
    attribute_mask = batch["attribute_mask"]
    center = masked_centernet_focal_loss(
        prediction["center_logits"], batch["center"], batch["detection_mask"]
    )

    if attribute_mask.any():
        dbh = functional.smooth_l1_loss(
            prediction["dbh_log1p"][attribute_mask], batch["dbh"][attribute_mask]
        )
    else:
        dbh = prediction["dbh_log1p"].sum() * 0

    genus_mask = attribute_mask & batch["genus"].ge(0)
    species_mask = attribute_mask & batch["species"].ge(0)
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
    total = (
        center_weight * center + dbh_weight * dbh + genus_weight * genus + species_weight * species
    )
    return {
        "loss": total,
        "center_loss": center,
        "dbh_loss": dbh,
        "genus_loss": genus,
        "species_loss": species,
    }
