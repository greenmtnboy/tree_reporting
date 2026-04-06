from __future__ import annotations

import os
import sys

import instructor

from ecoregion_matcher import EcoregionReference

from ._tree_enrichment_models import TreeEnrichment
from ._tree_enrichment_sources import SourceTexts, build_reference_text


DEFAULT_INSTRUCTOR_MODEL = os.getenv("TREE_ENRICHMENT_MODEL", "google/gemini-2.5-flash")
DEFAULT_VERTEX_PROJECT = os.getenv("TREE_ENRICHMENT_VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "preqldata"
DEFAULT_VERTEX_LOCATION = os.getenv("TREE_ENRICHMENT_VERTEX_LOCATION", "us-central1")


def normalize_instructor_model_name(model_name: str) -> str:
    cleaned = model_name.strip()
    if not cleaned:
        return DEFAULT_INSTRUCTOR_MODEL
    if "/" in cleaned:
        return cleaned
    return f"google/{cleaned}"


def create_instructor_client(model_name: str, project: str, location: str):
    normalized_model = normalize_instructor_model_name(model_name)
    return instructor.from_provider(
        normalized_model,
        vertexai=True,
        project=project,
        location=location,
    )


def format_ecoregion_candidates(candidates: list[EcoregionReference]) -> str:
    if not candidates:
        return "No shortlist available. Return an empty list unless the evidence is exceptionally clear."
    return "\n".join(
        f"- {candidate.ecoregion_id} | {candidate.ecoregion_name} | realm={candidate.realm or 'unknown'} | biome={candidate.biome or 'unknown'}"
        for candidate in candidates
    )


def build_enrichment_prompt_v2(
    scientific_name: str,
    wiki_name: str,
    reference_text: str,
    native_range_evidence: str,
    ecoregion_candidates: list[EcoregionReference],
) -> str:
    return (
        "You are enriching tree data for an urban forestry dataset.\n"
        "Extract structured information about this tree species from the reference text below.\n"
        "Be conservative with numeric estimates - use None if the sources do not clearly state a value.\n"
        "For mature height and canopy spread, extract min and max numeric values plus the original unit instead of converting units yourself.\n"
        "For growth rate, prefer annual numeric growth increments with units; only use the qualitative slow/moderate/fast field when the source explicitly uses those words.\n"
        "For bloom_months, return month numbers 1-12 rather than season text.\n"
        "For description, write a brief 1-3 sentence summary covering appearance, ecology, and urban planting relevance.\n"
        "Cultivar-level sources may be incomplete or may describe the parent hybrid/species instead of the exact named cultivar. Keep the full requested species name in mind, including any cultivar, and avoid confidently attributing details to the exact cultivar unless the source text supports it.\n\n"
        "For native_ecoregions, only use IDs from the shortlist below.\n"
        "Return an empty list when the native-range evidence is weak or ambiguous.\n"
        "Do not use planted range, cultivation range, or naturalized range as native range.\n\n"
        f"Species: {scientific_name}\n\n"
        f"Wikipedia lookup: {wiki_name}\n\n"
        f"Native-range evidence:\n{native_range_evidence}\n\n"
        f"Ecoregion shortlist:\n{format_ecoregion_candidates(ecoregion_candidates)}\n\n"
        f"Reference text:\n{reference_text}"
    )


def parse_enrichment_from_text_v2(
    scientific_name: str,
    wiki_name: str,
    texts: SourceTexts,
    client,
    native_range_evidence: str,
    ecoregion_candidates: list[EcoregionReference],
    print_full_context: bool = False,
) -> TreeEnrichment | None:
    reference_text = build_reference_text(texts)
    if not reference_text:
        return None

    if print_full_context:
        print("[debug] BEGIN LLM REFERENCE TEXT", file=sys.stderr)
        print(reference_text, file=sys.stderr)
        print("[debug] END LLM REFERENCE TEXT", file=sys.stderr)
        print("[debug] BEGIN NATIVE RANGE EVIDENCE", file=sys.stderr)
        print(native_range_evidence, file=sys.stderr)
        print("[debug] END NATIVE RANGE EVIDENCE", file=sys.stderr)
        print(f"[debug] ecoregion shortlist count: {len(ecoregion_candidates)}", file=sys.stderr)

    try:
        return client.chat.completions.create(
            response_model=TreeEnrichment,
            messages=[{
                "role": "user",
                "content": build_enrichment_prompt_v2(
                    scientific_name,
                    wiki_name,
                    reference_text,
                    native_range_evidence,
                    ecoregion_candidates,
                ),
            }],
        )
    except Exception as exc:
        print(f"  [error] instructor failed for {scientific_name!r}: {exc}", file=sys.stderr)
        return None


def debug_print_source_details(texts: SourceTexts, preview_chars: int = 500) -> None:
    entries = [
        ("Wikipedia", texts.wikipedia),
        ("POWO", texts.powo),
        ("GBIF", texts.gbif),
        ("SelecTree", texts.selectree),
    ]
    for label, text in entries:
        if text:
            preview = text[:preview_chars].replace("\n", "\\n")
            print(f"[debug] {label}: present ({len(text)} chars)", file=sys.stderr)
            print(f"[debug] {label} preview: {preview}", file=sys.stderr)
        else:
            print(f"[debug] {label}: missing", file=sys.stderr)
