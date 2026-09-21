import pytest
import torch

from urban_tree_ml.dataset import apply_dihedral


def _sample() -> dict[str, object]:
    grid = torch.arange(6).reshape(2, 3)
    return {
        "chip_id": "chip",
        "image": grid.unsqueeze(0),
        "center": grid,
        "detection_mask": grid,
        "dbh_mask": grid,
        "genus_mask": grid,
        "species_mask": grid,
        "dbh": grid,
        "genus": grid,
        "species": grid,
    }


@pytest.mark.parametrize('with_crowns', [False, True])
def test_dihedral_transform_keeps_all_target_fields_registered(with_crowns) -> None:
    sample = _sample()
    if with_crowns:
        sample.update(crown=sample['center'], crown_mask=sample['center'])
    transformed = apply_dihedral(sample, 5)
    expected = torch.flip(torch.rot90(_sample()["center"], 1, (-2, -1)), (-1,))

    assert transformed["chip_id"] == "chip"
    assert torch.equal(transformed["image"][0], expected)
    for key in (
        "center",
        "detection_mask",
        "dbh_mask",
        "genus_mask",
        "species_mask",
        "dbh",
        "genus",
        "species",
    ):
        assert torch.equal(transformed[key], expected)
    if with_crowns:
        assert torch.equal(transformed['crown'], expected)
        assert torch.equal(transformed['crown_mask'], expected)


def test_dihedral_transform_rejects_unknown_symmetry() -> None:
    with pytest.raises(ValueError, match="between 0 and 7"):
        apply_dihedral(_sample(), 8)
