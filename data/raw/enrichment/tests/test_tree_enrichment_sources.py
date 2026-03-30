#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["requests"]
# ///

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

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

    @patch.object(sources, "fetch_powo_taxon_text")
    @patch.object(sources.requests, "get")
    def test_fetch_powo_text_prefers_clean_full_name_match(
        self,
        mock_get: Mock,
        mock_fetch_taxon_text: Mock,
    ) -> None:
        mock_get.return_value = make_response(
            {
                "results": [
                    {"name": "Abies sachalinensis var. mayriana", "fqId": "powo-1"},
                    {"name": "Abies sachalinensis", "fqId": "powo-2"},
                ]
            }
        )
        mock_fetch_taxon_text.return_value = "powo exact"

        result = sources.fetch_powo_text("Abies sachalinensis var. mayriana")

        self.assertEqual("powo exact", result)
        mock_fetch_taxon_text.assert_called_once()
        matched_result, scientific_name, query_name = mock_fetch_taxon_text.call_args.args[:3]
        self.assertEqual("Abies sachalinensis var. mayriana", matched_result["name"])
        self.assertEqual("Abies sachalinensis var. mayriana", scientific_name)
        self.assertEqual("Abies sachalinensis var. mayriana", query_name)

    @patch.object(sources, "fetch_powo_taxon_text")
    @patch.object(sources.requests, "get")
    def test_fetch_powo_text_falls_back_to_first_two_results_per_source(
        self,
        mock_get: Mock,
        mock_fetch_taxon_text: Mock,
    ) -> None:
        search_payload = {
            "results": [
                {"name": "Abies sachalinensis", "fqId": "powo-1"},
                {"name": "Abies veitchii", "fqId": "powo-2"},
                {"name": "Abies mariesii", "fqId": "powo-3"},
            ]
        }
        mock_get.side_effect = [
            make_response(search_payload),
            make_response(search_payload),
        ]
        mock_fetch_taxon_text.side_effect = ["candidate one", "candidate two"]

        result = sources.fetch_powo_text("Abies sachalinensis var. mayriana")

        self.assertEqual(
            "POWO fallback candidate 1:\ncandidate one\n\nPOWO fallback candidate 2:\ncandidate two",
            result,
        )
        self.assertEqual(
            [
                call(
                    {"name": "Abies sachalinensis", "fqId": "powo-1"},
                    "Abies sachalinensis var. mayriana",
                    "Abies sachalinensis var. mayriana",
                    include_query_note=True,
                ),
                call(
                    {"name": "Abies veitchii", "fqId": "powo-2"},
                    "Abies sachalinensis var. mayriana",
                    "Abies sachalinensis var. mayriana",
                    include_query_note=False,
                ),
            ],
            mock_fetch_taxon_text.call_args_list,
        )


if __name__ == "__main__":
    unittest.main()
