import numpy as np

from urban_tree_ml.targets import DetectionMaskRegion, PointLabel, build_targets


def test_positive_unlabeled_mask_does_not_mark_green_pixels_as_background() -> None:
    ndvi = np.full((16, 16), 0.7, dtype=np.float32)
    ndvi[:, :4] = -0.2
    targets = build_targets(
        16,
        16,
        [PointLabel(x=12, y=8, dbh_log1p=2.0, genus_id=1, species_id=3)],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=ndvi,
        background_mode="ndvi_positive_unlabeled",
        background_ndvi_max=0.05,
    )

    assert targets["center"][4, 6] == 1
    assert targets["detection_mask"][4, 6] == 1
    assert targets["detection_mask"][0, 0] == 1  # trusted low-NDVI negative
    assert targets["detection_mask"][0, 4] == 0  # unlabeled vegetation is ignored
    assert targets["species"][4, 6] == 3
    assert targets["dbh_mask"][4, 6] == 1
    assert targets["genus_mask"][4, 6] == 1
    assert targets["species_mask"][4, 6] == 1


def test_detection_labels_do_not_require_dbh_or_taxonomy() -> None:
    targets = build_targets(
        16,
        16,
        [
            PointLabel(x=4, y=4),
            PointLabel(x=12, y=12, dbh_log1p=2.0, genus_id=1),
        ],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=np.zeros((16, 16), dtype=np.float32),
    )

    assert targets["center"][2, 2] == 1
    assert targets["dbh_mask"][2, 2] == 0
    assert targets["genus_mask"][2, 2] == 0
    assert targets["species_mask"][2, 2] == 0
    assert targets["center"][6, 6] == 1
    assert targets["dbh_mask"][6, 6] == 1
    assert targets["genus_mask"][6, 6] == 1
    assert targets["species_mask"][6, 6] == 0


def test_point_inside_chip_is_clamped_to_last_output_cell() -> None:
    targets = build_targets(
        8,
        8,
        [PointLabel(x=7.8, y=7.8)],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=np.zeros((8, 8), dtype=np.float32),
    )

    assert targets["center"][3, 3] == 1
    assert targets["detection_mask"][3, 3] == 1


def test_every_label_in_an_output_cell_collision_is_ignored() -> None:
    labels = [
        PointLabel(x=4.0, y=4.0, dbh_log1p=1.0, genus_id=0, species_id=0),
        PointLabel(x=4.4, y=4.4, dbh_log1p=3.0, genus_id=1, species_id=2),
    ]
    targets = build_targets(
        8,
        8,
        labels,
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=np.zeros((8, 8), dtype=np.float32),
    )

    assert targets["collision_cells"] == 1
    assert targets["collision_excluded_points"] == 2
    assert targets["center"][2, 2] == 0
    assert targets["detection_mask"][2, 2] == 0
    assert targets["dbh_mask"][2, 2] == 0
    assert targets["genus_mask"][2, 2] == 0
    assert targets["species_mask"][2, 2] == 0


def test_rejected_inventory_points_are_ignored_without_erasing_retained_positives() -> None:
    targets = build_targets(
        16,
        16,
        [PointLabel(x=8, y=8, dbh_log1p=2.0, genus_id=0, species_id=0)],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=4,
        ndvi=np.full((16, 16), -0.2, dtype=np.float32),
        ignored_locations=[(0, 0), (8, 8)],
    )

    assert targets["detection_mask"][0, 0] == 0
    assert targets["detection_mask"][4, 4] == 1
    assert targets["center"][4, 4] == 1


def test_manual_regions_override_automatic_background_and_preserve_positives() -> None:
    targets = build_targets(
        24,
        24,
        [PointLabel(x=20, y=20)],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=np.full((24, 24), -0.2, dtype=np.float32),
        mask_regions=[
            DetectionMaskRegion(x=4, y=4, radius=3, mode="protect"),
            DetectionMaskRegion(x=12, y=12, radius=3, mode="confirmed-background"),
            DetectionMaskRegion(x=20, y=20, radius=3, mode="protect"),
        ],
    )

    assert targets["detection_mask"][2, 2] == 0
    assert targets["detection_mask"][6, 6] == 1
    assert targets["center"][10, 10] == 1
    assert targets["detection_mask"][10, 10] == 1


def test_later_manual_region_wins_when_regions_overlap() -> None:
    targets = build_targets(
        16,
        16,
        [],
        stride=2,
        gaussian_sigma_px=1,
        supervision_radius_px=2,
        ndvi=np.full((16, 16), -0.2, dtype=np.float32),
        mask_regions=[
            DetectionMaskRegion(x=8, y=8, radius=4, mode="protect"),
            DetectionMaskRegion(x=8, y=8, radius=2, mode="confirmed-background"),
        ],
    )

    assert targets["detection_mask"][4, 4] == 1
    assert targets["detection_mask"][4, 2] == 0
