#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ecoregion_matcher import (
    build_native_range_evidence,
    make_ecoregion_reference,
    select_ecoregion_candidates,
)


ABIES_GRANDIS_POWO = """POWO matched name: Abies grandis
POWO author: (Douglas ex D.Don) Lindl.
POWO family: Pinaceae
POWO snippet: Abies grandis is a tree and grows primarily in the temperate biome.
Lifeform: tree
Climate: temperate
Taxonomic status: Accepted
Taxon remarks: SW. Canada to N. California
Classification: FAMILY: Pinaceae | GENUS: Abies | SPECIES: Abies grandis
Distribution natives: British Columbia, California, Idaho, Montana, Oregon, Washington
Distribution natives detail: name=British Columbia; tdwg=BRC; path=BRC_OO > BRC > NORTHERN_AMERICA > WESTERN_CANADA; establishment=Native | name=California; tdwg=CAL; path=CAL_OO > SOUTHWESTERN_USA > CAL > NORTHERN_AMERICA; establishment=Native | name=Idaho; tdwg=IDA; path=IDA > IDA_OO > NORTHWESTERN_USA > NORTHERN_AMERICA; establishment=Native | name=Montana; tdwg=MNT; path=MNT_OO > NORTHWESTERN_USA > NORTHERN_AMERICA > MNT; establishment=Native | name=Oregon; tdwg=ORE; path=NORTHWESTERN_USA > ORE_OO > ORE > NORTHERN_AMERICA; establishment=Native | name=Washington; tdwg=WAS; path=WAS > WAS_OO > NORTHWESTERN_USA > NORTHERN_AMERICA; establishment=Native
Distribution introduced: Czechia-Slovakia, Denmark, Germany, Great Britain, Ireland, Italy, Norway, Sweden, Switzerland
Distribution introduced detail: name=Germany; tdwg=GER; path=GER > MIDDLE_EUROPE > GER_OO > EUROPE; establishment=Introduced | name=Sweden; tdwg=SWE; path=NORTHERN_EUROPE > SWE > SWE_OO > EUROPE; establishment=Introduced"""


class EcoregionMatcherTests(unittest.TestCase):
    def test_build_native_range_evidence_keeps_structured_powo_lines(self) -> None:
        evidence = build_native_range_evidence(
            [("POWO", ABIES_GRANDIS_POWO), ("Wikipedia", "plain description only")],
            "fallback reference text",
        )

        self.assertIn("Climate: temperate", evidence)
        self.assertIn("Taxon remarks: SW. Canada to N. California", evidence)
        self.assertIn("Distribution natives: British Columbia, California", evidence)
        self.assertIn("POWO snippet: Abies grandis is a tree and grows primarily in the temperate biome.", evidence)
        self.assertNotIn("Classification:", evidence)

    def test_select_candidates_prefers_native_pacific_northwest_ecoregions(self) -> None:
        evidence = build_native_range_evidence([("POWO", ABIES_GRANDIS_POWO)], "")
        references = [
            make_ecoregion_reference(349, "British Columbia coastal conifer forests", "Nearctic", "Temperate Conifer Forests"),
            make_ecoregion_reference(352, "Central-Southern Cascades Forests", "Nearctic", "Temperate Conifer Forests"),
            make_ecoregion_reference(358, "North Cascades conifer forests", "Nearctic", "Temperate Conifer Forests"),
            make_ecoregion_reference(359, "Northern California coastal forests", "Nearctic", "Temperate Conifer Forests"),
            make_ecoregion_reference(361, "Northern Rockies conifer forests", "Nearctic", "Temperate Conifer Forests"),
            make_ecoregion_reference(422, "California coastal sage and chaparral", "Nearctic", "Mediterranean Forests, Woodlands & Scrub"),
            make_ecoregion_reference(708, "Scandinavian coastal conifer forests", "Palearctic", "Temperate Conifer Forests"),
        ]

        ranked_ids = [
            candidate.ecoregion_id
            for candidate in select_ecoregion_candidates(evidence, references, limit=7)
        ]

        self.assertIn(349, ranked_ids[:3])
        self.assertIn(359, ranked_ids[:5])
        self.assertLess(ranked_ids.index(349), ranked_ids.index(708))
        self.assertLess(ranked_ids.index(359), ranked_ids.index(422))

    def test_build_native_range_evidence_falls_back_when_sources_lack_geo_cues(self) -> None:
        fallback = "reference text fallback"
        evidence = build_native_range_evidence(
            [("POWO", "Lifeform: tree\nClassification: FAMILY: Pinaceae")],
            fallback,
        )
        self.assertEqual(fallback, evidence)


if __name__ == "__main__":
    unittest.main()
