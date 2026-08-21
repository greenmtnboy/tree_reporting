import torch

from urban_tree_ml.losses import multitask_loss
from urban_tree_ml.model import RawImageryTreeModel


def test_model_and_multitask_loss_shapes() -> None:
    model = RawImageryTreeModel(
        input_channels=4,
        feature_channels=32,
        genus_classes=3,
        species_classes=5,
        pretrained=False,
    ).eval()
    with torch.no_grad():
        prediction = model(torch.zeros(2, 4, 64, 64))

    assert prediction["center_logits"].shape == (2, 32, 32)
    assert prediction["dbh_log1p"].shape == (2, 32, 32)
    assert prediction["genus_logits"].shape == (2, 3, 32, 32)
    assert prediction["species_logits"].shape == (2, 5, 32, 32)

    center = torch.zeros(2, 32, 32)
    center[:, 8, 8] = 1
    attributes = torch.zeros(2, 32, 32, dtype=torch.bool)
    attributes[:, 8, 8] = True
    batch = {
        "center": center,
        "detection_mask": torch.ones_like(center),
        "attribute_mask": attributes,
        "dbh": torch.ones_like(center),
        "genus": torch.where(attributes, 1, -1),
        "species": torch.where(attributes, 2, -1),
    }
    losses = multitask_loss(
        prediction,
        batch,
        center_weight=1,
        dbh_weight=1,
        genus_weight=1,
        species_weight=1,
    )

    assert torch.isfinite(losses["loss"])
