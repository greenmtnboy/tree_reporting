from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Sequence

NATIVE_RANGE_LINE_HINTS = (
    "native",
    "distribution",
    "range",
    "habitat",
    "endemic",
    "origin",
    "occurs",
    "found in",
    "flora of",
)

_EVIDENCE_LINE_PREFIXES = (
    "Distribution natives:",
    "Distribution natives detail:",
    "Distribution endemic:",
    "Distribution endemic detail:",
    "Distribution introduced:",
    "Distribution introduced detail:",
    "Taxon remarks:",
    "Climate:",
)

_COMMON_STOPWORDS = {
    "and",
    "the",
    "with",
    "from",
    "that",
    "this",
    "into",
    "over",
    "under",
    "tree",
    "trees",
}

_NON_GEOGRAPHY_TOKENS = {
    "alpine",
    "basin",
    "basins",
    "boreal",
    "broadleaf",
    "chaparral",
    "conifer",
    "coniferous",
    "deciduous",
    "desert",
    "deserts",
    "dry",
    "flooded",
    "forest",
    "forests",
    "grassland",
    "grasslands",
    "mangroves",
    "mediterranean",
    "mixed",
    "moist",
    "montane",
    "plateau",
    "plateaus",
    "rain",
    "savanna",
    "savannas",
    "scrub",
    "shrubland",
    "shrublands",
    "steppe",
    "subtropical",
    "taiga",
    "temperate",
    "tropical",
    "upland",
    "valley",
    "wet",
    "woodland",
    "woodlands",
    "xeric",
}


@dataclass(frozen=True)
class EcoregionReference:
    ecoregion_id: int
    ecoregion_name: str
    realm: str | None
    biome: str | None
    normalized_name: str
    normalized_biome: str | None
    normalized_realm: str | None
    search_tokens: frozenset[str]
    geography_tokens: frozenset[str]
    biome_tokens: frozenset[str]


@dataclass(frozen=True)
class NativeRangeSignals:
    normalized_evidence: str
    general_tokens: frozenset[str]
    native_name_tokens: frozenset[str]
    native_path_tokens: frozenset[str]
    native_summary_tokens: frozenset[str]
    introduced_tokens: frozenset[str]
    biome_tokens: frozenset[str]


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def tokenize_text(value: str | None) -> frozenset[str]:
    return _tokenize(value, _COMMON_STOPWORDS)


def make_ecoregion_reference(
    ecoregion_id: int,
    ecoregion_name: str,
    realm: str | None,
    biome: str | None,
) -> EcoregionReference:
    normalized_name = normalize_text(ecoregion_name)
    normalized_biome = normalize_text(biome) or None
    normalized_realm = normalize_text(realm) or None
    search_text = " ".join(part for part in (ecoregion_name, realm, biome) if part)
    return EcoregionReference(
        ecoregion_id=ecoregion_id,
        ecoregion_name=ecoregion_name,
        realm=realm,
        biome=biome,
        normalized_name=normalized_name,
        normalized_biome=normalized_biome,
        normalized_realm=normalized_realm,
        search_tokens=tokenize_text(search_text),
        geography_tokens=_tokenize(ecoregion_name, _COMMON_STOPWORDS | _NON_GEOGRAPHY_TOKENS),
        biome_tokens=tokenize_text(biome),
    )


def build_native_range_evidence(
    source_entries: Sequence[tuple[str, str | None]],
    reference_text: str,
) -> str:
    parts: list[str] = []
    for label, text in source_entries:
        if not text:
            continue
        matching_lines = [
            line.strip()
            for line in text.splitlines()
            if _is_relevant_evidence_line(line.strip())
        ]
        if matching_lines:
            parts.append(f"{label} native-range cues:\n" + "\n".join(matching_lines[:16]))
    if parts:
        return "\n\n".join(parts)
    return reference_text


def extract_native_range_signals(native_range_evidence: str) -> NativeRangeSignals:
    native_names: list[str] = []
    native_paths: list[str] = []
    native_summaries: list[str] = []
    introduced_names: list[str] = []
    introduced_paths: list[str] = []
    biome_lines: list[str] = []

    for raw_line in native_range_evidence.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Distribution natives:") or line.startswith("Distribution endemic:"):
            native_names.extend(_split_csv_values(_suffix_after_colon(line)))
            native_summaries.append(_suffix_after_colon(line))
            continue
        if line.startswith("Distribution natives detail:") or line.startswith("Distribution endemic detail:"):
            names, paths = _parse_distribution_details(_suffix_after_colon(line))
            native_names.extend(names)
            native_paths.extend(paths)
            continue
        if line.startswith("Distribution introduced:"):
            introduced_names.extend(_split_csv_values(_suffix_after_colon(line)))
            continue
        if line.startswith("Distribution introduced detail:"):
            names, paths = _parse_distribution_details(_suffix_after_colon(line))
            introduced_names.extend(names)
            introduced_paths.extend(paths)
            continue
        if line.startswith("Taxon remarks:"):
            native_summaries.append(_suffix_after_colon(line))
            continue
        if line.startswith("Climate:"):
            biome_lines.append(_suffix_after_colon(line))
            continue
        if "biome" in line.lower():
            biome_lines.append(line)
            continue
        if any(hint in line.lower() for hint in NATIVE_RANGE_LINE_HINTS):
            native_summaries.append(line)

    return NativeRangeSignals(
        normalized_evidence=normalize_text(native_range_evidence),
        general_tokens=tokenize_text(native_range_evidence),
        native_name_tokens=_tokenize_many(native_names, _COMMON_STOPWORDS | _NON_GEOGRAPHY_TOKENS),
        native_path_tokens=_tokenize_many(native_paths, _COMMON_STOPWORDS | _NON_GEOGRAPHY_TOKENS),
        native_summary_tokens=_tokenize_many(native_summaries, _COMMON_STOPWORDS),
        introduced_tokens=_tokenize_many(
            [*introduced_names, *introduced_paths],
            _COMMON_STOPWORDS | _NON_GEOGRAPHY_TOKENS,
        ),
        biome_tokens=_tokenize_many(biome_lines, _COMMON_STOPWORDS),
    )


def select_ecoregion_candidates(
    native_range_evidence: str,
    references: Sequence[EcoregionReference],
    limit: int = 40,
) -> list[EcoregionReference]:
    if not references:
        return []

    signals = extract_native_range_signals(native_range_evidence)
    scored: list[tuple[int, EcoregionReference]] = []
    for ref in references:
        score = _score_reference(ref, signals)
        if score >= 4:
            scored.append((score, ref))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].ecoregion_name,
            item[1].ecoregion_id,
        )
    )
    return [ref for _, ref in scored[:limit]]


def _tokenize(value: str | None, stopwords: set[str]) -> frozenset[str]:
    return frozenset(
        token
        for token in normalize_text(value).split()
        if len(token) >= 3 and token not in stopwords
    )


def _tokenize_many(values: Sequence[str], stopwords: set[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(_tokenize(value, stopwords))
    return frozenset(tokens)


def _suffix_after_colon(line: str) -> str:
    _, _, suffix = line.partition(":")
    return suffix.strip()


def _split_csv_values(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_distribution_details(value: str) -> tuple[list[str], list[str]]:
    names: list[str] = []
    paths: list[str] = []
    for item in [part.strip() for part in value.split(" | ") if part.strip()]:
        name_match = re.search(r"name=([^;]+)", item)
        path_match = re.search(r"path=([^;]+)", item)
        if name_match:
            names.append(name_match.group(1).strip())
        if path_match:
            paths.extend(
                segment.strip()
                for segment in path_match.group(1).split(">")
                if segment.strip()
            )
    return names, paths


def _is_relevant_evidence_line(line: str) -> bool:
    if not line:
        return False
    if line.startswith(_EVIDENCE_LINE_PREFIXES):
        return True
    lowered = line.lower()
    if line.startswith("POWO snippet:") and "biome" in lowered:
        return True
    return any(hint in lowered for hint in NATIVE_RANGE_LINE_HINTS)


def _score_reference(ref: EcoregionReference, signals: NativeRangeSignals) -> int:
    score = 0

    if ref.normalized_name and ref.normalized_name in signals.normalized_evidence:
        score += 12
    if ref.normalized_biome and ref.normalized_biome in signals.normalized_evidence:
        score += 6
    if ref.normalized_realm and ref.normalized_realm in signals.normalized_evidence:
        score += 2

    native_name_overlap = len(ref.geography_tokens.intersection(signals.native_name_tokens))
    native_path_overlap = len(ref.geography_tokens.intersection(signals.native_path_tokens))
    native_summary_overlap = len(ref.geography_tokens.intersection(signals.native_summary_tokens))
    biome_overlap = len(ref.biome_tokens.intersection(signals.biome_tokens))
    general_overlap = len(ref.search_tokens.intersection(signals.general_tokens))
    introduced_overlap = len(ref.geography_tokens.intersection(signals.introduced_tokens))

    score += native_name_overlap * 5
    score += native_path_overlap * 4
    score += min(native_summary_overlap, 3) * 2
    score += biome_overlap * 4
    score += min(general_overlap, 4)
    score -= introduced_overlap * 4

    if native_name_overlap and biome_overlap:
        score += 4
    if native_path_overlap and biome_overlap:
        score += 3

    return score
