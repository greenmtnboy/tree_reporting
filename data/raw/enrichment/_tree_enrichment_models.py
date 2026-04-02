from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class TreeEnrichment(BaseModel):
    common_names: list[str] = Field(
        description="All known common names for this species, most familiar first"
    )
    description: Optional[str] = Field(
        None,
        description=(
            "Brief 1-3 sentence note describing appearance, ecology, and any especially relevant "
            "urban planting considerations such as drought, roots, wildlife, litter, or site fit."
        ),
    )
    native_ecoregions: list[int] = Field(
        default_factory=list,
        description=(
            "List of ecoregion_id values where the species is native. "
            "Only choose IDs from the provided shortlist and leave this empty if the evidence is weak or ambiguous."
        ),
    )
    is_evergreen: Optional[bool] = Field(
        None, description="True if evergreen, False if deciduous, None if unknown or semi-evergreen"
    )
    mature_height_min_value: Optional[float] = Field(
        None, description="Minimum reported mature height numeric value from sources, before unit conversion."
    )
    mature_height_max_value: Optional[float] = Field(
        None, description="Maximum reported mature height numeric value from sources, before unit conversion."
    )
    mature_height_unit: Optional[Literal["ft", "m", "cm", "in"]] = Field(
        None, description="Unit for mature height values. Prefer ft or m when sources allow."
    )
    canopy_spread_min_value: Optional[float] = Field(
        None, description="Minimum reported mature canopy spread numeric value from sources, before unit conversion."
    )
    canopy_spread_max_value: Optional[float] = Field(
        None, description="Maximum reported mature canopy spread numeric value from sources, before unit conversion."
    )
    canopy_spread_unit: Optional[Literal["ft", "m", "cm", "in"]] = Field(
        None, description="Unit for canopy spread values. Prefer ft or m when sources allow."
    )
    growth_rate_min_value: Optional[float] = Field(
        None, description="Minimum reported annual growth increment numeric value from sources."
    )
    growth_rate_max_value: Optional[float] = Field(
        None, description="Maximum reported annual growth increment numeric value from sources."
    )
    growth_rate_unit: Optional[Literal["ft_per_year", "m_per_year", "cm_per_year", "in_per_year"]] = Field(
        None, description="Unit for annual growth increment values."
    )
    growth_rate: Optional[Literal["slow", "moderate", "fast"]] = Field(
        None,
        description="Qualitative growth rate label only when sources clearly give slow, moderate, or fast directly.",
    )
    lifespan_years: Optional[str] = Field(
        None, description="Typical lifespan, e.g. '50-100', '200+', 'short-lived'"
    )
    drought_tolerance: Optional[Literal["low", "moderate", "high"]] = Field(None)
    water_needs: Optional[Literal["low", "moderate", "high"]] = Field(
        None,
        description="Irrigation needs: low = drought-adapted once established, moderate = average garden watering, high = consistently moist soil required.",
    )
    sun_exposure: list[Literal["full_sun", "part_shade", "full_shade"]] = Field(
        default_factory=list,
        description="Tolerated light conditions. Include all that apply: full_sun (6+ hrs direct), part_shade (3-6 hrs), full_shade (<3 hrs).",
    )
    soil_preferences: list[str] = Field(
        default_factory=list,
        description="Preferred soil types using standard horticultural terms, e.g. ['well-drained', 'loamy', 'clay-tolerant', 'sandy', 'acidic']. Return [] if unknown.",
    )
    root_behavior: Optional[Literal["non-invasive", "moderate", "invasive"]] = Field(
        None,
        description="Root aggressiveness in urban settings: non-invasive = deep/compact roots, moderate = some surface spreading, invasive = aggressive surface roots that damage pavement or structures.",
    )
    coastal_tolerance: Optional[bool] = Field(
        None, description="True if the species tolerates salt spray and coastal wind exposure."
    )
    salt_tolerance: Optional[bool] = Field(
        None, description="True if the species tolerates road salt or saline soil conditions."
    )
    pollution_tolerance: Optional[Literal["low", "moderate", "high"]] = Field(
        None, description="Tolerance for urban air pollution and compacted urban soils."
    )
    usda_zone_min: Optional[int] = Field(
        None, description="Minimum USDA hardiness zone as an integer (e.g. 5 for zone 5). Omit the letter suffix."
    )
    usda_zone_max: Optional[int] = Field(
        None, description="Maximum USDA hardiness zone as an integer (e.g. 11). Omit the letter suffix."
    )
    bloom_months: list[int] = Field(
        default_factory=list,
        description=(
            "Blooming months as integers 1-12. Convert season text into months. "
            "Return [] if bloom timing is unknown."
        ),
    )
    wildlife_value: Optional[Literal["low", "moderate", "high"]] = Field(
        None, description="Value to local urban wildlife: pollinators, birds, small mammals"
    )
    fire_risk: Optional[Literal["low", "moderate", "high"]] = Field(
        None, description="Fire risk / flammability rating relevant to urban California"
    )
    tree_form: Literal["broadleaf", "conifer", "palm", "columnar", "ornamental", "spreading", "weeping", "multi_trunk", "default"] = Field(
        description=(
            "Visual silhouette tree form for map icon rendering. "
            "palm = fan fronds on tall slender trunk (palms). "
            "broadleaf = round canopy on short trunk (oaks, maples, most deciduous). "
            "conifer = triangular/spire shape (pines, cypress, firs). "
            "columnar = narrow upright oval, taller than wide (Italian cypress, columnar trees). "
            "ornamental = small flowering tree with visible blooms (cherry, plum, crabapple). "
            "spreading = wide flat canopy, broader than tall (plane trees, acacias). "
            "weeping = drooping canopy with pendulous branches (weeping willow, bottlebrush forms). "
            "multi_trunk = clustered trunks supporting a broad crown (crape myrtle, paperbark forms). "
            "default = generic round tree when shape is unclear."
        )
    )
