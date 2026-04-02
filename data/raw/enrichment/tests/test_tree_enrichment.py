#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy", "pydantic"]
# ///

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import enrichment._tree_enrichment_llm as llm
import enrichment._tree_enrichment_sources as sources
from enrichment._tree_enrichment_helpers import (
    convert_length_range_to_feet,
    normalize_bloom_months,
    normalize_growth_rate,
)


class TreeEnrichmentTests(unittest.TestCase):
    def test_default_instructor_model_is_flash(self) -> None:
        self.assertEqual("google/gemini-2.5-flash", llm.DEFAULT_INSTRUCTOR_MODEL)

    def test_normalize_enrichment_search_name_trims_after_species(self) -> None:
        self.assertEqual(
            "Abies nordmanniana",
            sources.normalize_enrichment_search_name("Abies nordmanniana ssp. bornmuelleriana"),
        )
        self.assertEqual(
            "Lyonothamnus floribundus",
            sources.normalize_enrichment_search_name("Lyonothamnus floribundus subsp. asplenifolius"),
        )
        self.assertEqual(
            "Viburnum odoratissimum",
            sources.normalize_enrichment_search_name("Viburnum odoratissimum var. awabuki"),
        )
        self.assertEqual(
            "Abies grandis",
            sources.normalize_enrichment_search_name("Abies grandis"),
        )

    def test_normalize_enrichment_search_name_keeps_hybrids_and_placeholders(self) -> None:
        self.assertEqual(
            "Acer x freemanii",
            sources.normalize_enrichment_search_name("Acer x freemanii"),
        )
        self.assertEqual(
            "Quercus spp.",
            sources.normalize_enrichment_search_name("Quercus spp."),
        )
        self.assertEqual(
            "Other",
            sources.normalize_enrichment_search_name("Other"),
        )

    def test_normalize_bloom_months_filters_and_sorts(self) -> None:
        self.assertEqual(
            [3, 4, 5],
            normalize_bloom_months([5, 3, 4, 3, 0, 13]),
        )
        self.assertIsNone(normalize_bloom_months([]))

    def test_convert_length_range_to_feet_handles_metric_ranges(self) -> None:
        min_ft, max_ft = convert_length_range_to_feet(40, 70, "m")
        self.assertAlmostEqual(131.23, min_ft, places=2)
        self.assertAlmostEqual(229.66, max_ft, places=2)

    def test_normalize_growth_rate_prefers_numeric_units(self) -> None:
        self.assertEqual(
            "moderate",
            normalize_growth_rate(30, 60, "cm_per_year", None),
        )
        self.assertEqual(
            "fast",
            normalize_growth_rate(3, 4, "ft_per_year", "slow"),
        )
        self.assertEqual(
            "slow",
            normalize_growth_rate(None, None, None, "slow"),
        )

    def test_normalize_instructor_model_name_adds_google_prefix(self) -> None:
        self.assertEqual(
            "google/gemini-2.5-flash",
            llm.normalize_instructor_model_name("gemini-2.5-flash"),
        )
        self.assertEqual(
            "google/gemini-2.5-pro",
            llm.normalize_instructor_model_name("google/gemini-2.5-pro"),
        )

    def test_create_instructor_client_uses_normalized_model(self) -> None:
        mock_from_provider = Mock(return_value="client")
        with patch.object(
            llm,
            "instructor",
            types.SimpleNamespace(from_provider=mock_from_provider),
        ):
            client = llm.create_instructor_client(
                "gemini-2.5-flash",
                "demo-project",
                "us-central1",
            )

        self.assertEqual("client", client)
        mock_from_provider.assert_called_once_with(
            "google/gemini-2.5-flash",
            vertexai=True,
            project="demo-project",
            location="us-central1",
        )


if __name__ == "__main__":
    unittest.main()
