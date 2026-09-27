from urban_tree_ml.point_predict import (
    MultiInventory,
    cities_meeting,
    city_boxes,
    grid_tile_origin,
    owning_city,
    point_chip_id,
)


def test_grid_tile_keeps_the_point_a_quarter_tile_from_the_edge():
    tile = 153.6
    for x, y in [(326_170.0, 4_689_700.0), (326_247.0, 4_689_639.0), (0.1, -0.1)]:
        west, north = grid_tile_origin(x, y, tile)
        assert west + tile / 4 - 1e-6 <= x <= west + 3 * tile / 4 + 1e-6
        assert north - 3 * tile / 4 - 1e-6 <= y <= north - tile / 4 + 1e-6


def test_nearby_points_share_a_tile():
    assert grid_tile_origin(1000.0, 2000.0, 153.6) == grid_tile_origin(1010.0, 1990.0, 153.6)


def test_chip_id_names_the_grid_cell():
    assert point_chip_id(26919, 326170.4, 4689792.0) == "p26919_e326170_n4689792"


def test_city_boxes_are_read_from_the_ingest_library():
    envelopes, territory = city_boxes()
    assert "USBOS" in envelopes and "USSFO" in envelopes
    assert all(len(box) == 4 for box in envelopes.values())
    assert all(boxes for boxes in territory.values())


def test_owning_city_prefers_carved_territory():
    envelopes = {"AAAAA": (0.0, 2.0, 0.0, 2.0), "BBBBB": (1.0, 3.0, 1.0, 3.0)}
    territory = {"BBBBB": ((1.5, 3.0, 1.5, 3.0),)}
    assert owning_city(1.6, 1.6, envelopes, territory) == "BBBBB"
    assert owning_city(1.2, 1.2, envelopes, territory) == "AAAAA"
    assert owning_city(5.0, 5.0, envelopes, territory) is None


def test_a_tile_on_a_boundary_meets_both_cities():
    envelopes = {"AAAAA": (0.0, 1.0, 0.0, 1.0), "BBBBB": (0.0, 1.0, 1.0, 2.0), "CCCCC": (5.0, 6.0, 5.0, 6.0)}
    bounds = {"south": 0.4, "north": 0.5, "west": 0.95, "east": 1.05}
    assert cities_meeting(bounds, envelopes) == ["AAAAA", "BBBBB"]


class _Reader:
    def __init__(self, version, trees):
        self.version, self.trees = version, trees

    def within(self, bounds):
        return self.trees


def test_multi_inventory_merges_cities_once_per_tree():
    inventory = MultiInventory({
        "BBBBB": _Reader("v2", [{"treeId": "b1"}, {"treeId": "shared"}]),
        "AAAAA": _Reader("v1", [{"treeId": "a1"}, {"treeId": "shared"}]),
    })
    assert [t["treeId"] for t in inventory.within({})] == ["a1", "b1", "shared"]
    assert inventory.version == "AAAAA:v1;BBBBB:v2"
