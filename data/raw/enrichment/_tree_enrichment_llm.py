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
        "For common_names, always return at least one name, most familiar first. A named hybrid or a rarely cultivated species often has no established English name of its own: fall back to a name it plainly inherits - a Quercus hybrid is a hybrid oak, a Rhododendron hybrid is a hybrid rhododendron - or to an anglicised form of the scientific name. Do not coin a name that implies a different taxon, and return an empty list if the name given is not a real species at all.\n"
        "For tree_form, pick the silhouette that best fits the mature habit. Use \"default\" only when the sources say nothing about growth habit; it renders as a generic icon on the map.\n"
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


# Asked once, most of the obscure taxa came back with an empty `common_names`
# and a "default" tree_form -- not because they have no name, but because the
# prompt never mentioned either field and the model had an easy out.  Both are
# required by the response model, and an empty list satisfies `list[str]`.
#
# The retry deliberately keeps the honest escape hatch open.  Some values in
# the published inventories are not taxa at all but chimeras welded from two
# real names -- "Erythrina camaldulensis" (that is a Eucalyptus), "Pinus abies"
# (a Picea), "Acer implexa" (an Acacia).  There is nothing to find, and pushing
# harder for a name would get one invented: the Orania failure in miniature, a
# plausible and specific and wrong label on every tree carrying that value.
COMMON_NAME_RETRY_NOTE = (
    "\n\nYour previous answer returned no common name. Most species and named "
    "hybrids either have one or inherit one from a parent or genus. Give the "
    "most widely used English name you can support from the sources, or an "
    "anglicised form of the scientific name. If the name above is not a real "
    "taxon - a genus and an epithet from two different species welded together, "
    "for instance - return an empty list and say so in the description."
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

    prompt = build_enrichment_prompt_v2(
        scientific_name,
        wiki_name,
        reference_text,
        native_range_evidence,
        ecoregion_candidates,
    )

    def ask(content: str) -> TreeEnrichment | None:
        try:
            return client.chat.completions.create(
                response_model=TreeEnrichment,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            print(f"  [error] instructor failed for {scientific_name!r}: {exc}", file=sys.stderr)
            return None

    enrichment = ask(prompt)
    if enrichment is None or enrichment.common_names:
        return enrichment

    print(f"    [retry] no common name for {scientific_name!r}, asking again", file=sys.stderr)
    retried = ask(prompt + COMMON_NAME_RETRY_NOTE)
    if retried is None:
        return enrichment
    if not retried.common_names:
        # Taken at its word: asked twice, told twice there is no name.  The row
        # still lands, so the species is not re-queued for ever.
        print(f"    [retry] {scientific_name!r} still has no common name", file=sys.stderr)
    return retried


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
