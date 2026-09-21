from datetime import date

import pandas as pd
import pytest

from urban_tree_ml.inventory import planted_after_imagery


def test_cutoff_preserves_unknown_and_same_day():
    frame = pd.DataFrame({"plant_date": [None, "bad", "2022-05-18", "2022-05-19T23:00:00Z", "2022-05-20", "2025-01-01"]}, index=[10, 20, 30, 40, 50, 60])
    assert planted_after_imagery(frame, date(2022, 5, 19)).tolist() == [False, False, False, False, True, True]
    assert not planted_after_imagery(frame, None).any()


def test_configured_cutoff_fails_closed_on_missing_column():
    with pytest.raises(ValueError, match="plant_date is required"):
        planted_after_imagery(pd.DataFrame({"tree_id": ["x"]}), date(2022, 5, 19))
    assert not planted_after_imagery(pd.DataFrame({"tree_id": ["x"]}), None).any()
