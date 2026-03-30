from __future__ import annotations

import re

from ._tree_enrichment_constants import SYNONYMS
from ._tree_enrichment_models import TreeEnrichment


LENGTH_TO_FEET = {
    "ft": 1.0,
    "m": 3.28084,
    "cm": 0.0328084,
    "in": 1.0 / 12.0,
}

GROWTH_RATE_TO_FT_PER_YEAR = {
    "ft_per_year": 1.0,
    "m_per_year": 3.28084,
    "cm_per_year": 0.0328084,
    "in_per_year": 1.0 / 12.0,
}

TREE_FORM_MAP: dict[str, str] = {
    "palm": "palm",
    "broadleaf": "broadleaf",
    "conifer": "conifer",
    "spreading": "spreading",
    "coniferous": "conifer",
    "columnar": "columnar",
    "ornamental": "ornamental",
    "weeping": "weeping",
    "multi_trunk": "multi_trunk",
    "default": "default",
}


def parse_scientific_name(q_species: str) -> str:
    return q_species.split("::")[0].strip()


def split_scientific_parts(scientific_name: str) -> tuple[str | None, str | None]:
    tokens = scientific_name.split()
    genus = tokens[0] if tokens else None
    species_epithet = tokens[1] if len(tokens) >= 2 else None
    return genus, species_epithet


def map_tree_form(tree_form: str | None) -> str | None:
    if tree_form is None:
        return None
    return TREE_FORM_MAP.get(tree_form, tree_form)


def parse_lifespan_range(lifespan_years: str | None) -> tuple[int | None, int | None]:
    if not lifespan_years:
        return None, None
    numbers = [int(match) for match in re.findall(r"\d+", lifespan_years)]
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    if len(numbers) == 1:
        if "+" in lifespan_years:
            return numbers[0], None
        return numbers[0], numbers[0]
    return None, None


def normalize_bloom_months(bloom_months: list[int] | None) -> list[int] | None:
    if not bloom_months:
        return None
    cleaned = sorted({month for month in bloom_months if isinstance(month, int) and 1 <= month <= 12})
    return cleaned or None


def convert_length_range_to_feet(
    min_value: float | None,
    max_value: float | None,
    unit: str | None,
) -> tuple[float | None, float | None]:
    if unit is None:
        return None, None
    factor = LENGTH_TO_FEET.get(unit)
    if factor is None:
        return None, None
    converted_min = float(min_value) * factor if min_value is not None else None
    converted_max = float(max_value) * factor if max_value is not None else None
    if converted_min is None and converted_max is not None:
        converted_min = converted_max
    if converted_max is None and converted_min is not None:
        converted_max = converted_min
    if converted_min is not None and converted_max is not None and converted_min > converted_max:
        converted_min, converted_max = converted_max, converted_min
    return converted_min, converted_max


def normalize_growth_rate(
    min_value: float | None,
    max_value: float | None,
    unit: str | None,
    fallback_label: str | None,
) -> str | None:
    if unit in GROWTH_RATE_TO_FT_PER_YEAR:
        factor = GROWTH_RATE_TO_FT_PER_YEAR[unit]
        converted_values = [
            float(value) * factor
            for value in (min_value, max_value)
            if value is not None
        ]
        if converted_values:
            typical_ft_per_year = sum(converted_values) / len(converted_values)
            if typical_ft_per_year < 1.0:
                return "slow"
            if typical_ft_per_year <= 2.0:
                return "moderate"
            return "fast"
    if fallback_label in {"slow", "moderate", "fast"}:
        return fallback_label
    return None


def map_wikipedia_lookup(scientific_name: str) -> str:
    return SYNONYMS.get(scientific_name.lower(), scientific_name)


def compute_is_complete(enrichment: TreeEnrichment) -> tuple[bool, list[str]]:
    checks = [
        ("common_names", bool(enrichment.common_names)),
        ("description", bool(enrichment.description and enrichment.description.strip())),
        ("is_evergreen", enrichment.is_evergreen is not None),
        (
            "mature_height_max_ft",
            convert_length_range_to_feet(
                enrichment.mature_height_min_value,
                enrichment.mature_height_max_value,
                enrichment.mature_height_unit,
            )[1] is not None,
        ),
        (
            "canopy_spread_max_ft",
            convert_length_range_to_feet(
                enrichment.canopy_spread_min_value,
                enrichment.canopy_spread_max_value,
                enrichment.canopy_spread_unit,
            )[1] is not None,
        ),
        (
            "growth_rate",
            normalize_growth_rate(
                enrichment.growth_rate_min_value,
                enrichment.growth_rate_max_value,
                enrichment.growth_rate_unit,
                enrichment.growth_rate,
            ) is not None,
        ),
        ("drought_tolerance", enrichment.drought_tolerance is not None),
        ("tree_form", map_tree_form(enrichment.tree_form) is not None),
    ]
    missing = [name for name, ok in checks if not ok]
    return len(missing) == 0, missing
