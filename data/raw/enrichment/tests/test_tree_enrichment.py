#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "duckdb", "google-genai>=1.30", "pytrilogy", "pydantic"]
# ///

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import enrichment._tree_enrichment_llm as llm
import enrichment._tree_enrichment_sources as sources
from enrichment._tree_enrichment_models import TreeEnrichment
from enrichment._tree_enrichment_helpers import (
    convert_length_range_to_feet,
    normalize_bloom_months,
    normalize_growth_rate,
)


class TreeEnrichmentTests(unittest.TestCase):
    def test_default_llm_model_is_flash(self) -> None:
        self.assertEqual("google/gemini-2.5-flash", llm.DEFAULT_LLM_MODEL)

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

    def test_normalize_model_name_adds_google_prefix(self) -> None:
        self.assertEqual(
            "google/gemini-2.5-flash",
            llm.normalize_model_name("gemini-2.5-flash"),
        )
        self.assertEqual(
            "google/gemini-2.5-pro",
            llm.normalize_model_name("google/gemini-2.5-pro"),
        )

    def test_normalize_model_name_rejects_other_providers(self) -> None:
        with self.assertRaises(ValueError):
            llm.normalize_model_name("openai/gpt-4o")

    def _client_with_replies(self, *replies: str) -> tuple[llm.GeminiStructuredClient, Mock]:
        genai_client = Mock()
        genai_client.models.generate_content.side_effect = [types.SimpleNamespace(text=r) for r in replies]
        with patch("google.genai.Client", return_value=genai_client) as client_cls:
            client = llm.create_llm_client("gemini-2.5-flash", "demo-project", "us-central1")
        client_cls.assert_called_once_with(vertexai=True, project="demo-project", location="us-central1")
        return client, genai_client

    def test_generate_validates_against_response_model(self) -> None:
        client, genai_client = self._client_with_replies('{"common_names": ["Coast live oak"], "tree_form": "spreading"}')

        result = client.generate(TreeEnrichment, "prompt")

        self.assertEqual(["Coast live oak"], result.common_names)
        self.assertEqual("spreading", result.tree_form)
        call = genai_client.models.generate_content.call_args
        self.assertEqual("gemini-2.5-flash", call.kwargs["model"])
        self.assertEqual("application/json", call.kwargs["config"].response_mime_type)
        self.assertEqual(TreeEnrichment.model_json_schema(), call.kwargs["config"].response_json_schema)

    def test_generate_sends_validation_error_back_and_retries(self) -> None:
        client, genai_client = self._client_with_replies(
            '{"common_names": ["Coast live oak"], "tree_form": "round"}',
            '{"common_names": ["Coast live oak"], "tree_form": "broadleaf"}',
        )

        result = client.generate(TreeEnrichment, "prompt")

        self.assertEqual("broadleaf", result.tree_form)
        retry_contents = genai_client.models.generate_content.call_args_list[1].kwargs["contents"]
        self.assertEqual(["user", "model", "user"], [c.role for c in retry_contents])
        self.assertIn("tree_form", retry_contents[2].parts[0].text)

    def test_generate_gives_up_after_max_attempts(self) -> None:
        bad = '{"common_names": []}'
        client, genai_client = self._client_with_replies(bad, bad, bad)

        with self.assertRaises(RuntimeError):
            client.generate(TreeEnrichment, "prompt")
        self.assertEqual(llm.DEFAULT_MAX_ATTEMPTS, genai_client.models.generate_content.call_count)

if __name__ == "__main__":
    unittest.main()
