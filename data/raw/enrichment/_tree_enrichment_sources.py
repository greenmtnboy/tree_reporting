from __future__ import annotations

from dataclasses import dataclass
import html
import re

import requests


HEADERS = {"User-Agent": "sf-tree-enrichment/1.0 (github.com/sf-tree-reporting)"}


@dataclass
class SourceTexts:
    wikipedia: str | None
    powo: str | None
    gbif: str | None
    selectree: str | None


def normalize_enrichment_search_name(scientific_name: str) -> str:
    tokens = scientific_name.split()
    if len(tokens) < 2:
        return scientific_name
    genus, species_epithet = tokens[0], tokens[1]
    if not re.fullmatch(r"[A-Z][A-Za-z.-]*", genus):
        return scientific_name
    if not re.fullmatch(r"[a-z-]+", species_epithet):
        return scientific_name
    if species_epithet in {"sp", "sp.", "spp", "spp.", "x", "cf", "aff", "other"}:
        return scientific_name
    return f"{genus} {species_epithet}"


def enrichment_query_candidates(scientific_name: str) -> list[str]:
    candidates: list[str] = []
    for candidate in [scientific_name.strip(), normalize_enrichment_search_name(scientific_name)]:
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def normalize_taxon_match_name(name: str | None) -> str:
    if not name:
        return ""
    text = html.unescape(str(name))
    text = text.replace(chr(215), "x")
    text = re.sub(r"[`'\"“”‘’]", "", text)
    text = re.sub(r"\bsubspecies\b", "subsp.", text, flags=re.IGNORECASE)
    text = re.sub(r"\bvariety\b", "var.", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def is_clean_taxon_match(scientific_name: str, candidate_name: str | None) -> bool:
    return bool(candidate_name) and normalize_taxon_match_name(candidate_name) == normalize_taxon_match_name(scientific_name)


def fetch_wikipedia_text(scientific_name: str) -> str | None:
    for query_name in enrichment_query_candidates(scientific_name):
        slug = query_name.replace(" ", "_")
        summary_extract: str | None = None

        response = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}",
            headers=HEADERS,
            timeout=10,
        )
        if response.status_code == 200:
            data = response.json()
            extract = data.get("extract", "")
            if extract:
                summary_extract = extract

        response = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "extracts",
                "exintro": True,
                "explaintext": True,
                "titles": query_name,
                "format": "json",
                "redirects": 1,
            },
            headers=HEADERS,
            timeout=10,
        )
        if response.status_code == 200:
            pages = response.json().get("query", {}).get("pages", {})
            for page in pages.values():
                if page.get("pageid", -1) == -1:
                    continue
                extract = page.get("extract", "")
                if not extract:
                    continue
                best = summary_extract if summary_extract and len(summary_extract) > len(extract) else extract
                if query_name != scientific_name:
                    return f"Wikipedia search query normalized to: {query_name}\n{best}"
                return best

        if summary_extract:
            if query_name != scientific_name:
                return f"Wikipedia search query normalized to: {query_name}\n{summary_extract}"
            return summary_extract

    return None


def fetch_powo_taxon_text(
    result: dict,
    scientific_name: str,
    query_name: str,
    include_query_note: bool = True,
) -> str | None:
    matched_fq_id = result.get("fqId")
    if not matched_fq_id:
        return None
    accepted_ref = result.get("synonymOf") if not result.get("accepted", True) else None
    fq_id = accepted_ref.get("fqId") if isinstance(accepted_ref, dict) and accepted_ref.get("fqId") else matched_fq_id

    parts: list[str] = []
    if include_query_note and query_name != scientific_name:
        parts.append(f"POWO search query normalized to: {query_name}")
    if result.get("name"):
        parts.append(f"POWO matched name: {result['name']}")
    if result.get("author"):
        parts.append(f"POWO author: {result['author']}")
    if result.get("family"):
        parts.append(f"POWO family: {result['family']}")
    if accepted_ref:
        if accepted_ref.get("name"):
            parts.append(f"POWO accepted name via synonym: {accepted_ref['name']}")
        if accepted_ref.get("author"):
            parts.append(f"POWO accepted author via synonym: {accepted_ref['author']}")
    snippet = result.get("snippet")
    if isinstance(snippet, str) and snippet.strip():
        parts.append(f"POWO snippet: {snippet.replace('<b>', '').replace('</b>', '')}")

    response = requests.get(
        f"https://powo.science.kew.org/api/2/taxon/{fq_id}",
        params={"fields": "descriptions,distribution"},
        headers=HEADERS,
        timeout=10,
    )
    if response.status_code != 200:
        return None
    data = response.json()
    accepted_data = data.get("accepted")
    if (
        data.get("taxonomicStatus")
        and "synonym" in str(data.get("taxonomicStatus")).lower()
        and isinstance(accepted_data, dict)
        and accepted_data.get("fqId")
        and accepted_data.get("fqId") != fq_id
    ):
        accepted_response = requests.get(
            f"https://powo.science.kew.org/api/2/taxon/{accepted_data['fqId']}",
            params={"fields": "descriptions,distribution"},
            headers=HEADERS,
            timeout=10,
        )
        if accepted_response.status_code == 200:
            data = accepted_response.json()
            if accepted_data.get("name"):
                parts.append(f"POWO accepted name from taxon record: {accepted_data['name']}")
            if accepted_data.get("author"):
                parts.append(f"POWO accepted author from taxon record: {accepted_data['author']}")

    descriptions = data.get("descriptions", {})
    if isinstance(descriptions, dict):
        for source_name, source_payload in descriptions.items():
            if not isinstance(source_payload, dict):
                continue
            source_descriptions = source_payload.get("descriptions", {})
            if not isinstance(source_descriptions, dict):
                continue
            for characteristic, items in source_descriptions.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("description", "")
                    if text:
                        parts.append(f"{source_name}/{characteristic}: {text}")

    if data.get("lifeform"):
        parts.append(f"Lifeform: {data['lifeform']}")
    if data.get("climate"):
        parts.append(f"Climate: {data['climate']}")
    if data.get("species"):
        parts.append(f"Species epithet: {data['species']}")
    if data.get("taxonomicStatus"):
        parts.append(f"Taxonomic status: {data['taxonomicStatus']}")
    if data.get("taxonRemarks"):
        parts.append(f"Taxon remarks: {data['taxonRemarks']}")
    locations = data.get("locations")
    if isinstance(locations, list) and locations:
        parts.append(f"POWO location codes: {', '.join(str(code) for code in locations[:20])}")
    classification = data.get("classification")
    if isinstance(classification, list) and classification:
        labels = [
            f"{entry.get('rank')}: {entry.get('name')}"
            for entry in classification
            if isinstance(entry, dict) and entry.get("rank") and entry.get("name")
        ]
        if labels:
            parts.append(f"Classification: {' | '.join(labels[:8])}")

    distribution = data.get("distribution", {})
    if isinstance(distribution, dict):
        for bucket in ("natives", "introduced", "extinct", "uncertain"):
            entries = distribution.get(bucket, [])
            if not isinstance(entries, list) or not entries:
                continue
            names = [entry.get("name") for entry in entries if isinstance(entry, dict) and entry.get("name")]
            if names:
                parts.append(f"Distribution {bucket}: {', '.join(names[:20])}")
            detailed_entries = []
            for entry in entries[:12]:
                if not isinstance(entry, dict):
                    continue
                detail_bits = []
                if entry.get("name"):
                    detail_bits.append(f"name={entry['name']}")
                if entry.get("tdwgCode"):
                    detail_bits.append(f"tdwg={entry['tdwgCode']}")
                if entry.get("locationTree"):
                    detail_bits.append("path=" + " > ".join(str(node) for node in entry["locationTree"][:10]))
                if entry.get("establishment"):
                    detail_bits.append(f"establishment={entry['establishment']}")
                if detail_bits:
                    detailed_entries.append("; ".join(detail_bits))
            if detailed_entries:
                parts.append(f"Distribution {bucket} detail: {' | '.join(detailed_entries)}")

    return "\n".join(parts) if parts else None


def fetch_powo_text(scientific_name: str) -> str | None:
    try:
        fallback_results: list[tuple[str, dict]] = []
        for query_name in enrichment_query_candidates(scientific_name):
            response = requests.get(
                "https://powo.science.kew.org/api/2/search",
                params={"q": query_name, "f": "species_f"},
                headers=HEADERS,
                timeout=10,
            )
            if response.status_code != 200:
                continue
            results = response.json().get("results", [])
            if not results:
                continue

            clean_match = next(
                (
                    result
                    for result in results
                    if is_clean_taxon_match(scientific_name, result.get("name"))
                    or is_clean_taxon_match(
                        scientific_name,
                        result.get("synonymOf", {}).get("name") if isinstance(result.get("synonymOf"), dict) else None,
                    )
                ),
                None,
            )
            if clean_match:
                return fetch_powo_taxon_text(clean_match, scientific_name, query_name)

            if not fallback_results:
                fallback_results = [(query_name, result) for result in results[:2] if isinstance(result, dict)]

        fallback_texts = []
        for idx, (query_name, result) in enumerate(fallback_results, start=1):
            text = fetch_powo_taxon_text(
                result,
                scientific_name,
                query_name,
                include_query_note=(idx == 1),
            )
            if text:
                fallback_texts.append(f"POWO fallback candidate {idx}:\n{text}")
        return "\n\n".join(fallback_texts) if fallback_texts else None
    except Exception:
        return None


def fetch_gbif_text(scientific_name: str) -> str | None:
    try:
        fallback_text: str | None = None
        for query_name in enrichment_query_candidates(scientific_name):
            response = requests.get(
                "https://api.gbif.org/v1/species/match",
                params={"name": query_name, "verbose": "true"},
                headers=HEADERS,
                timeout=10,
            )
            if response.status_code != 200:
                continue
            data = response.json()
            usage_key = data.get("usageKey") or data.get("speciesKey")
            if not usage_key:
                continue

            parts = []
            if query_name != scientific_name:
                parts.append(f"GBIF search query normalized to: {query_name}")
            if data.get("scientificName"):
                parts.append(f"Matched scientific name: {data['scientificName']}")
            if data.get("canonicalName"):
                parts.append(f"Canonical name: {data['canonicalName']}")
            if data.get("family"):
                parts.append(f"Family: {data['family']}")
            if data.get("order"):
                parts.append(f"Order: {data['order']}")
            if data.get("status"):
                parts.append(f"Taxonomic status: {data['status']}")
            if data.get("confidence") is not None:
                parts.append(f"Match confidence: {data['confidence']}")

            alternatives = data.get("alternatives")
            if isinstance(alternatives, list) and alternatives:
                alt_names = [alt.get("scientificName") for alt in alternatives[:3] if isinstance(alt, dict) and alt.get("scientificName")]
                if alt_names:
                    parts.append(f"Alternative matches: {' | '.join(alt_names)}")

            try:
                names_response = requests.get(
                    f"https://api.gbif.org/v1/species/{usage_key}/vernacularNames",
                    params={"limit": 20},
                    headers=HEADERS,
                    timeout=10,
                )
                if names_response.status_code == 200:
                    names_payload = names_response.json()
                    rows = names_payload.get("results", []) if isinstance(names_payload, dict) else []
                    names = []
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        lang = row.get("language")
                        name = row.get("vernacularName")
                        if name and lang and str(lang).lower().startswith("en"):
                            names.append(name)
                    if names:
                        uniq = []
                        seen = set()
                        for name in names:
                            key = name.strip().lower()
                            if key in seen:
                                continue
                            seen.add(key)
                            uniq.append(name.strip())
                        if uniq:
                            parts.append(f"GBIF vernacular names (en): {', '.join(uniq[:8])}")
            except Exception:
                pass

            text = "\n".join(parts) if parts else None
            if not text:
                continue
            if is_clean_taxon_match(scientific_name, data.get("scientificName")):
                return text
            if fallback_text is None:
                fallback_text = text

        return fallback_text
    except Exception:
        return None


def _clean_selectree_value(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    text = str(value)
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _build_selectree_entry(row: dict, scientific_name: str, query_name: str, include_query_note: bool = True) -> str | None:
    tree_id = row.get("tree_id")
    if not tree_id:
        return None

    parts = [
        f"SelecTree tree_id: {tree_id}",
        f"SelecTree accepted scientific: {_clean_selectree_value(row.get('accepted_scientific') or row.get('name_concat') or scientific_name)}",
    ]
    if include_query_note and query_name != scientific_name:
        parts.insert(0, f"SelecTree search query normalized to: {query_name}")

    if row.get("common"):
        parts.append(f"SelecTree common name: {_clean_selectree_value(row['common'])}")
    if row.get("family"):
        parts.append(f"SelecTree family: {_clean_selectree_value(row['family'])}")
    if row.get("height_high"):
        parts.append(f"SelecTree reported max height: {row['height_high']} ft")

    detail_response = requests.get(
        f"https://selectree.calpoly.edu/api/tree/detail/{tree_id}",
        headers=HEADERS,
        timeout=12,
    )
    if detail_response.status_code == 200:
        detail = detail_response.json()
        if isinstance(detail, dict):
            if detail.get("memo"):
                parts.append(f"SelecTree memo: {_clean_selectree_value(detail['memo'])}")
            if detail.get("native_range"):
                parts.append(f"SelecTree native range: {_clean_selectree_value(detail['native_range'])}")
            if detail.get("foliage_type"):
                parts.append(f"SelecTree foliage type: {_clean_selectree_value(detail['foliage_type'])}")
            if detail.get("growth_rate_low") or detail.get("growth_rate_high"):
                parts.append(
                    f"SelecTree growth rate range: {detail.get('growth_rate_low')} to {detail.get('growth_rate_high')}"
                )
            if detail.get("width_low") or detail.get("width_high"):
                parts.append(f"SelecTree canopy width range: {detail.get('width_low')} to {detail.get('width_high')} ft")
            if detail.get("height_low") or detail.get("height_high"):
                parts.append(f"SelecTree height range: {detail.get('height_low')} to {detail.get('height_high')} ft")
            if detail.get("water_use"):
                parts.append(f"SelecTree water use: {_clean_selectree_value(detail['water_use'])}")
            if detail.get("flower_time"):
                parts.append(f"SelecTree flower time: {_clean_selectree_value(detail['flower_time'])}")
            if detail.get("flower_showiness"):
                parts.append(f"SelecTree flower showiness: {_clean_selectree_value(detail['flower_showiness'])}")
            if detail.get("fruiting_time"):
                parts.append(f"SelecTree fruiting time: {_clean_selectree_value(detail['fruiting_time'])}")
            if detail.get("attracts_wildlife"):
                parts.append(f"SelecTree attracts wildlife: {_clean_selectree_value(detail['attracts_wildlife'])}")
            if detail.get("disease_resistant"):
                parts.append(f"SelecTree disease resistant: {_clean_selectree_value(detail['disease_resistant'])}")
            if detail.get("pest_resistant"):
                parts.append(f"SelecTree pest resistant: {_clean_selectree_value(detail['pest_resistant'])}")

            primary_common = detail.get("primary_common") or {}
            if isinstance(primary_common, dict) and primary_common.get("common"):
                parts.append(f"SelecTree primary common: {_clean_selectree_value(primary_common['common'])}")
            other_common = detail.get("other_common") or []
            if isinstance(other_common, list):
                names = [
                    _clean_selectree_value(other.get("common"))
                    for other in other_common
                    if isinstance(other, dict) and other.get("common")
                ]
                if names:
                    parts.append(f"SelecTree other common names: {', '.join(names[:10])}")

    return "\n".join(parts)


def fetch_selectree_text(scientific_name: str) -> str | None:
    try:
        fallback_rows: list[tuple[str, dict]] = []
        for query_name in enrichment_query_candidates(scientific_name):
            response = requests.get(
                "https://selectree.calpoly.edu/api/tree/search-by-name-multiresult",
                params={
                    "region": "",
                    "searchTerm": query_name,
                    "activePage": 1,
                    "resultsPerPage": 30,
                    "sort": 1,
                },
                headers=HEADERS,
                timeout=12,
            )
            if response.status_code != 200:
                continue

            payload = response.json()
            rows = payload.get("pageResults", []) if isinstance(payload, dict) else []
            if not rows:
                continue

            clean_match = next(
                (
                    row
                    for row in rows
                    if is_clean_taxon_match(scientific_name, _clean_selectree_value(row.get("accepted_scientific")))
                    or is_clean_taxon_match(scientific_name, _clean_selectree_value(row.get("name_concat")))
                ),
                None,
            )
            if clean_match:
                return _build_selectree_entry(clean_match, scientific_name, query_name)

            if not fallback_rows:
                fallback_rows = [(query_name, row) for row in rows[:2] if isinstance(row, dict)]

        fallback_texts = []
        for idx, (query_name, row) in enumerate(fallback_rows, start=1):
            text = _build_selectree_entry(
                row,
                scientific_name,
                query_name,
                include_query_note=(idx == 1),
            )
            if text:
                fallback_texts.append(f"SelecTree fallback candidate {idx}:\n{text}")
        return "\n\n".join(fallback_texts) if fallback_texts else None
    except Exception:
        return None


def gather_source_texts(scientific_name: str, wiki_name: str) -> SourceTexts:
    return SourceTexts(
        wikipedia=fetch_wikipedia_text(wiki_name),
        powo=fetch_powo_text(scientific_name),
        gbif=fetch_gbif_text(scientific_name),
        selectree=fetch_selectree_text(scientific_name),
    )


def source_labels(texts: SourceTexts) -> list[str]:
    labels = []
    if texts.wikipedia:
        labels.append("Wikipedia")
    if texts.powo:
        labels.append("POWO")
    if texts.gbif:
        labels.append("GBIF")
    if texts.selectree:
        labels.append("SelecTree")
    return labels


def build_reference_text(texts: SourceTexts) -> str:
    context_parts = []
    if texts.wikipedia:
        context_parts.append(f"Wikipedia:\n{texts.wikipedia}")
    if texts.powo:
        context_parts.append(f"Plants of the World Online (POWO / Kew):\n{texts.powo}")
    if texts.gbif:
        context_parts.append(f"GBIF Species Match:\n{texts.gbif}")
    if texts.selectree:
        context_parts.append(f"SelecTree:\n{texts.selectree}")
    return "\n\n".join(context_parts)
