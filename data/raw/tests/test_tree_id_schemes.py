"""The two per-city halves of the `tree_id` grain fix.

`enforce_tree_schema` now refuses a repeated or null `tree_id` for every city
(see `TestTreeIdGrain` in `test_ingest_shared.py`).  That check is only useful
if each city actually has an id scheme that satisfies it, and two cities needed
one written: Washington DC had to change which field it keys on, and Boston had
to drop the rows its portal leaves unidentified.

Both are a handful of lines and neither is reachable from the ingest without a
network fetch, so they are pinned here directly.
"""

from pathlib import Path
import importlib.util
import sys

import pyarrow as pa

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dc = _load("washingtondc_tree_info", "uswas/washingtondc_tree_info.py")
boston = _load("boston_tree_info", "usbos/boston_tree_info.py")


class TestWashingtonDcTreeId:
    """DC keys on GLOBALID, not FACILITYID.

    FACILITYID is a *facility* id: 29.8% of the layer has none and 8,280 rows
    shared one with a different tree, so it violated both halves of the grain
    at once.  GLOBALID is populated and distinct on every row of the layer.
    """

    def test_esri_braces_and_case_are_stripped(self):
        """Esri renders a GlobalID braced and upper-case."""
        assert (
            dc.tree_id_for("{926038DA-1234-4C56-89AB-CDEF01234567}")
            == "was-926038da-1234-4c56-89ab-cdef01234567"
        )

    def test_an_unbraced_id_is_accepted_too(self):
        assert dc.tree_id_for("926038DA-0001") == "was-926038da-0001"

    def test_surrounding_whitespace_is_stripped(self):
        assert dc.tree_id_for("  {926038DA-0001}  ") == "was-926038da-0001"

    def test_a_missing_id_stays_none(self):
        """None rather than a synthesised id: `enforce_tree_schema` then raises,
        which is the point -- a DC row with no GlobalID means the layer changed."""
        assert dc.tree_id_for(None) is None
        assert dc.tree_id_for("") is None

    def test_distinct_global_ids_stay_distinct(self):
        """Normalisation must not collapse two ids onto one."""
        ids = {
            dc.tree_id_for(v)
            for v in ("{AAAA-1}", "{AAAA-2}", "aaaa-3", "{AAAA-4}")
        }
        assert len(ids) == 4


class TestBostonDropsUnidentified:
    """43 of ~55k Boston rows carry an empty `id`.

    They were already being lost in the grain join, silently; dropping them in
    the ingest is the same outcome with a count in the log.
    """

    @staticmethod
    def _table(ids: list[str | None]) -> pa.Table:
        return pa.table({"id": pa.array(ids, type=pa.string()),
                         "spp_bot": ["Acer rubrum"] * len(ids)})

    def test_null_and_empty_ids_are_dropped(self):
        out = boston.drop_unidentified(self._table(["b-1", None, "", "b-2"]))
        assert out.column("id").to_pylist() == ["b-1", "b-2"]

    def test_identified_rows_are_untouched(self):
        table = self._table(["b-1", "b-2", "b-3"])
        assert boston.drop_unidentified(table).num_rows == 3

    def test_the_drop_is_reported(self, capsys):
        """Silently dropping rows is the failure this replaced, so the count
        has to reach the refresh log."""
        boston.drop_unidentified(self._table(["b-1", None, ""]))
        assert "dropped 2 row(s)" in capsys.readouterr().err

    def test_nothing_is_reported_when_nothing_is_dropped(self, capsys):
        """A clean export should be silent, so the line means something when it
        does appear in a refresh log."""
        boston.drop_unidentified(self._table(["b-1", "b-2"]))
        assert capsys.readouterr().err == ""

    def test_a_table_without_an_id_column_passes_through(self):
        table = pa.table({"spp_bot": ["Acer rubrum"]})
        assert boston.drop_unidentified(table).num_rows == 1

    def test_the_id_column_is_found_case_insensitively(self):
        table = pa.table({"ID": pa.array(["b-1", None], type=pa.string())})
        assert boston.drop_unidentified(table).num_rows == 1
