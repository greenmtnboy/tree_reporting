"""Paired sample and explicit, reproducible region selection invariants."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boston_canopy_grid import matched_comparison, select_four


class PairedCanopyGridTests(unittest.TestCase):
    def test_missing_crowns_or_heat_remove_cell_from_both_metrics(self):
        cells = [{"id": str(i), "eligible": True, "crown_completeness": .98,
                  "canopy_percent": i*i, "tree_density": i, "temperature": 50-i*i}
                 for i in range(1, 10)]
        cells += [dict(cells[0], id="missing", crown_completeness=.949),
                  dict(cells[0], id="heat", eligible=False),
                  dict(cells[0], id="nan", canopy_percent=float("nan"))]
        rows, scores = matched_comparison(cells)
        self.assertEqual([r["id"] for r in rows], [str(i) for i in range(1, 10)])
        self.assertEqual(scores["tree_density"]["n"], 9)
        self.assertEqual(scores["canopy_percent"]["n"], 9)
        self.assertAlmostEqual(scores["canopy_percent"]["pearson"], -1)

    @staticmethod
    def view(name, density=-.8, canopy=-.9, coverage=.98, paired=24, temperature=35):
        return {"place": name, "baseline_valid_land_fraction": coverage,
                "baseline_temperature": temperature, "n": 25, "paired_n": paired,
                "paired_associations": {"tree_density": {"spearman": density},
                                        "canopy_percent": {"spearman": canopy}}}

    def test_selection_uses_weaker_score_and_rejects_poor_coverage(self):
        views = [self.view("A", temperature=40), self.view("B", temperature=39),
                 self.view("C", temperature=38), self.view("D", temperature=37),
                 self.view("E", density=-.6, canopy=-.99),
                 self.view("Missing heat", density=-.99, canopy=-.99, coverage=.89),
                 self.view("Missing crowns", density=-.99, canopy=-.99, paired=18)]
        self.assertEqual([v["place"] for v in select_four(views)], ["A", "B", "C", "D"])
        self.assertEqual([v["place"] for v in select_four(list(reversed(views)))], ["A", "B", "C", "D"])

    def test_selection_never_relaxes_requirements_to_fill_four(self):
        with self.assertRaisesRegex(ValueError, "Only 3"):
            select_four([self.view(name) for name in ("A", "B", "C")])


if __name__ == "__main__":
    unittest.main()
