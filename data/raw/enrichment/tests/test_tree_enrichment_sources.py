#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import enrichment._tree_enrichment_sources as sources


def make_response(payload: dict, status_code: int = 200) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


class TreeEnrichmentSourceTests(unittest.TestCase):
    def test_enrichment_query_candidates_prefers_full_name(self) -> None:
        self.assertEqual(
            [
                "Abies sachalinensis var. mayriana",
                "Abies sachalinensis",
            ],
            sources.enrichment_query_candidates("Abies sachalinensis var. mayriana"),
        )
        self.assertEqual(
            ["Abies grandis"],
            sources.enrichment_query_candidates("Abies grandis"),
        )

    def test_clean_taxon_match_normalizes_rank_labels(self) -> None:
        self.assertTrue(
            sources.is_clean_taxon_match(
                "Abies sachalinensis subsp. mayriana",
                "Abies sachalinensis subspecies mayriana",
            )
        )
        self.assertTrue(
            sources.is_clean_taxon_match(
                "Viburnum odoratissimum var. awabuki",
                "Viburnum odoratissimum variety awabuki",
            )
        )


if __name__ == "__main__":
    unittest.main()
