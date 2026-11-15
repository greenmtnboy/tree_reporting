import torch
import pytest

from urban_tree_ml.losses import multitask_loss
from urban_tree_ml.model import RawImageryTreeModel


@pytest.mark.parametrize('backbone',['resnet34','convnext_tiny','swin_tiny'])
@pytest.mark.parametrize('crown_head', [False, True])
def test_model_and_multitask_loss_shapes(backbone, crown_head) -> None:
    model = RawImageryTreeModel(
        input_channels=4,
        feature_channels=32,
        genus_classes=3,
        species_classes=5,
        pretrained=False,
        backbone=backbone,
        crown_head=crown_head,
    ).eval()
    with torch.no_grad():
        prediction = model(torch.zeros(2, 4, 64, 64))

    assert prediction["center_logits"].shape == (2, 32, 32)
    assert prediction["dbh_log1p"].shape == (2, 32, 32)
    assert prediction["genus_logits"].shape == (2, 3, 32, 32)
    assert prediction["species_logits"].shape == (2, 5, 32, 32)
    if crown_head:
        assert prediction['crown_log1p'].shape == (2, 32, 32)
    else:
        assert 'crown_log1p' not in prediction

    center = torch.zeros(2, 32, 32)
    center[:, 8, 8] = 1
    attributes = torch.zeros(2, 32, 32, dtype=torch.bool)
    attributes[:, 8, 8] = True
    batch = {
        "center": center,
        "detection_mask": torch.ones_like(center),
        "dbh_mask": attributes,
        "genus_mask": attributes,
        "species_mask": attributes,
        "dbh": torch.ones_like(center),
        "genus": torch.where(attributes, 1, -1),
        "species": torch.where(attributes, 2, -1),
        "crown": torch.ones_like(center),
        "crown_mask": attributes.float(),
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
    assert torch.isfinite(losses['crown_loss'])
    if not crown_head:
        assert losses['crown_loss'] == 0


@pytest.mark.parametrize('backbone', ['resnet34', 'swin_tiny'])
@pytest.mark.parametrize('start,size', [(16, 32), (14, 36), (0, 20), (41, 23), (0, 64), (7, 1)])
def test_a_region_is_the_whole_pass_cropped(backbone, start, size) -> None:
    """Bulk prediction keeps part of each window; the region pass must be the
    whole pass there, at the image's border too."""
    torch.manual_seed(0)
    model = RawImageryTreeModel(
        input_channels=4, feature_channels=16, genus_classes=3, species_classes=5,
        pretrained=False, backbone=backbone, crown_head=True,
    ).eval()
    image = torch.randn(2, 4, 128, 128)
    with torch.no_grad():
        whole = model(image)
        region = model.forward_region(image, start, size)

    assert set(region) == set(whole)
    for key, value in whole.items():
        crop = value[..., start:start + size, start:start + size]
        assert region[key].shape == crop.shape
        assert torch.allclose(region[key], crop, atol=1e-5), key


def test_a_region_must_lie_inside_the_output() -> None:
    model = RawImageryTreeModel(
        input_channels=4, feature_channels=16, genus_classes=3, species_classes=5,
        pretrained=False, backbone='resnet34',
    ).eval()
    with torch.no_grad(), pytest.raises(ValueError):
        model.forward_region(torch.zeros(1, 4, 64, 64), 20, 20)
