#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy"]
# [tool.uv]
# exclude-newer = "14 days"
# ///

import sys
import os
import argparse
import re
import html
import subprocess
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
import requests
import duckdb
from datetime import datetime, timezone
from typing import Literal, Optional
from dataclasses import dataclass
from pydantic import BaseModel, Field
import instructor
from ecoregion_matcher import (
    EcoregionReference,
    build_native_range_evidence as build_native_range_evidence_for_entries,
    make_ecoregion_reference,
    select_ecoregion_candidates as select_ecoregion_candidates_from_evidence,
)
from _ecoregion_shared import (
    LAYER_QUERY_URL,
    REMOTE_ECOREGION_PARQUET,
)
from _tree_shared import (
    ENRICHMENT_PARQUET,
    ENRICHMENT_GCS_URI,
    TREE_INFO_PARQUET,
    SKIP_SPECIES,
    SPECIES_EXCLUSION_SQL,
    should_skip_species,
)

ICON_SIZE = 48
DEFAULT_INSTRUCTOR_MODEL = os.getenv("TREE_ENRICHMENT_MODEL", "google/gemini-2.5-flash")
DEFAULT_VERTEX_PROJECT = os.getenv("TREE_ENRICHMENT_VERTEX_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT") or "preqldata"
DEFAULT_VERTEX_LOCATION = os.getenv("TREE_ENRICHMENT_VERTEX_LOCATION", "us-central1")

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


SYNONYMS = {
    "brisbane box": "Lophostemon confertus",
    "tree": "Tree",
    "abutilon hybridum": "Abutilon",
    "acacia baileyana 'purpurea'": "Acacia baileyana",
    "acacia spp": "Acacia",
    "acer ginnela": "Acer ginnala",
    "acer palmatum 'bloodgood'": "Acer palmatum",
    "acer palmatum 'sango kaku'": "Acer palmatum",
    "acer platanoides 'crimson king'": "Acer platanoides",
    "acer rubrum 'armstrong'": "Acer rubrum",
    "acer rubrum 'autumn glory'": "Acer rubrum",
    "acer rubrum 'october glory'": "Acer rubrum",
    "acer rubrum 'red sunset'": "Acer rubrum",
    "acer spp": "Maple",
    "acer x 'autumn blaze'": "Acer × freemanii",
    "acer x freemanii 'autumn blaze'": "Acer × freemanii",
    "aesculus spp": "Aesculus",
    "aesculus x carnea 'briotii'": "Aesculus × carnea",
    "aesculus x carnea 'o'neill'": "Aesculus × carnea",
    "agonis flexuosa 'after dark'": "Agonis flexuosa",
    "albizia distachya": "Paraserianthes lophantha",
    "angohpora spp.": "Angophora",
    "arbutus 'marina'": "Arbutus",
    "arbutus unedo 'compacta'": "Arbutus unedo",
    "arctostaphylos manzanita 'dr hurd'": "Arctostaphylos manzanita",
    "bambusa spp": "Bamboo",
    "betula spp": "Birch",
    "brahea aramata": "Brahea armata",
    "brahea brandegeei": "Brahea brandegeei",
    "brugmansia spp": "Brugmansia",
    "callistemon 'jeffers'": "Callistemon",
    "carpinus betulus 'fastigiata'": "Carpinus betulus",
    "caryota maxima 'himalaya'": "Caryota",
    "casurina stricta": "Allocasuarina verticillata",
    "ceanothus 'ray hartman'": "Ceanothus",
    "ceanothus sps": "Ceanothus",
    "cedrus atlantica glauca": "Cedrus atlantica",
    "cercis canadensis 'forest pansy'": "Cercis canadensis",
    "cercis canadensis 'oklahoma'": "Cercis canadensis",
    "chionanthus retusa": "Chionanthus retusus",
    "citrus aurantifolia 'bearss'": "Persian lime",
    "citrus spp": "Citrus",
    "citrus x limon 'lisbon'": "Lemon",
    "citrus x meyeri 'improved'": "Meyer lemon",
    "cornus spp": "Cornus (genus)",
    "cornus nuttallii x florida 'eddie's white wonder'": "Cornus × elwinmoorei",
    "cotinus coggygria 'royal purple'": "Cotinus coggygria",
    "cotoneaster spp": "Cotoneaster",
    "crataegus laevigata 'paul's scarlet'": "Crataegus laevigata",
    "crateagus spp": "Crataegus",
    "cupressus spp": "Cupressus",
    "dodonaea viscosa 'purpurea'": "Dodonaea viscosa",
    "dypsis cabadae": "Dypsis cabadae",
    "eriobotrya deflexa 'coppertone'": "Eriobotrya deflexa",
    "eucalyptus spp": "Eucalyptus",
    "eucalyptus lehmanni": "Eucalyptus lehmannii",
    "eucalyptus leucoxylon mac 'rosea'": "Eucalyptus leucoxylon",
    "eucalyptus macarthuri": "Eucalyptus macarthurii",
    "eucalyptus simmondsi": "Eucalyptus simmondsii",
    "fagus sylvatica 'red obelisk'": "Fagus sylvatica",
    "ficus spp.": "Ficus",
    "ficus carica 'black mission'": "Black Mission fig",
    "ficus carica 'brown turkey'": "Brown Turkey fig",
    "ficus laurel": "Ficus microcarpa",
    "ficus microcarpa 'retusa'": "Ficus microcarpa",
    "ficus microcarpa nitida 'green gem'": "Ficus microcarpa",
    "ficus retusa nitida": "Ficus microcarpa",
    "fraxinus americana 'autumn purple'": "Fraxinus americana",
    "fraxinus holotricha": "Fraxinus holotricha",
    "fraxinus oxycarpa 'raywood'": "Fraxinus angustifolia",
    "fraxinus spp": "Ash",
    "fraxinus uhdei 'tomlinson'": "Fraxinus uhdei",
    "fraxinus velutina 'glabra'": "Fraxinus velutina",
    "fraxinus velutina 'modesto'": "Fraxinus velutina",
    "fraxinus x moraine": "Fraxinus holotricha",
    "fremontodendron spp": "Fremontodendron",
    "garrya elliptica 'evie'": "Garrya elliptica",
    "garrya elliptica 'james roof'": "Garrya elliptica",
    "geijera spp": "Geijera",
    "ginkgo biloba 'autumn gold'": "Ginkgo biloba",
    "ginkgo biloba 'autumn sentinel'": "Ginkgo biloba",
    "ginkgo biloba 'fairmont'": "Ginkgo biloba",
    "ginkgo biloba 'princeton sentry'": "Ginkgo biloba",
    "ginkgo biloba 'saratoga'": "Ginkgo biloba",
    "gleditsia triacanthos 'aurea'": "Honey locust",
    "gleditsia triacanthos 'shademaster'": "Honey locust",
    "gleditsia triacanthos 'sunburst'": "Honey locust",
    "grevillea spp": "Grevillea",
    "ilex altaclarensis 'wilsonii'": "Ilex × altaclerensis",
    "ilex spp": "Ilex",
    "juglans 'paradox'": "Juglans",
    "juniperus scopulorum 'pat": "Juniperus scopulorum",
    "lagerstroemia indica 'natchez'": "Lagerstroemia indica",
    "lagerstroemia indica 'tuscarora'": "Lagerstroemia indica",
    "lagerstroemia spp": "Lagerstroemia",
    "lagerstroemia x 'tuscarora'": "Lagerstroemia",
    "laurus x 'saratoga'": "Laurus nobilis",
    "leptospermum quinquenervia": "Melaleuca quinquenervia",
    "leptospermum scoparium 'helene strybing'": "Leptospermum scoparium",
    "leptospermum scoparium 'ruby glow'": "Leptospermum scoparium",
    "leptospermum scoparium 'snow white'": "Leptospermum scoparium",
    "leucadendron 'gold strike'": "Leucadendron",
    "leucodendron argenteum": "Leucadendron argenteum",
    "liquidambar styraciflua 'burgundy'": "Liquidambar styraciflua",
    "liquidambar styraciflua 'festival'": "Liquidambar styraciflua",
    "liquidambar styraciflua 'palo alto'": "Liquidambar styraciflua",
    "liquidambar styraciflua 'rotundiloba'": "Liquidambar styraciflua",
    "liquidambar styraciflua 'slender silhoutte'": "Liquidambar styraciflua",
    "lyonothamnus floribundus subsp. asplenifolius": "Lyonothamnus floribundus",
    "magnolia doltsopa 'silvercloud'": "Magnolia doltsopa",
    "magnolia grandiflora 'd.d. blanchard'": "Magnolia grandiflora",
    "magnolia grandiflora 'little gem'": "Magnolia grandiflora",
    "magnolia grandiflora 'majestic beauty'": "Magnolia grandiflora",
    "magnolia grandiflora 'russet'": "Magnolia grandiflora",
    "magnolia grandiflora 'saint mary'": "Magnolia grandiflora",
    "magnolia grandiflora 'samuel sommer'": "Magnolia grandiflora",
    "magnolia grandiflora 'timeless beauty'": "Magnolia grandiflora",
    "magnolia sargentiana 'robusta'": "Magnolia sargentiana",
    "magnolia spp": "Magnolia",
    "magnolia x foggii 'jack fogg'": "Magnolia × foggii",
    "magnolia x soulangiana 'rustica rubra'": "Magnolia × soulangiana",
    "magnolia x soulangiana": "Magnolia × soulangiana",
    "malus 'gala'": "Gala (apple)",
    "malus floribunda 'prairie fire'": "Malus floribunda",
    "malus x 'callaway'": "Malus",
    "maytenus boaria 'green showers'": "Maytenus boaria",
    "melaleuca spp": "Melaleuca",
    "melaleuca styphelliodes": "Melaleuca styphelioides",
    "melia azerdarach": "Melia azedarach",
    "metasequoia glyplostroboides": "Metasequoia glyptostroboides",
    "metrosideros excelsa 'aurea'": "Metrosideros excelsa",
    "metrosideros spp": "Metrosideros",
    "michelia champaca 'alba'": "Magnolia × alba",
    "morus alba 'fruitless'": "Morus alba",
    "new zealand tea tree": "Leptospermum scoparium",
    "olea majestic beauty": "Olea europaea",
    "olea europaea 'fruitless'": "Olea europaea",
    "olea europaea 'majestic beauty'": "Olea europaea",
    "olea europaea 'swan hill'": "Olea europaea",
    "olea europaea 'wilsonii'": "Olea europaea",
    "palm (unknown genus)": "Arecaceae",
    "persea americana 'stewart'": "Avocado",
    "phoenix spp": "Phoenix (plant)",
    "picea spp": "Spruce",
    "pinus spp": "Pine",
    "pinus thunbergii 'thunderhead'": "Pinus thunbergii",
    "pittosporum spp": "Pittosporum",
    "platanus x hispanica 'bloodgood'": "Platanus × acerifolia",
    "platanus x hispanica 'columbia'": "Platanus × acerifolia",
    "platanus x hispanica 'yarwood'": "Platanus × acerifolia",
    "podocarpus gracilor": "Afrocarpus gracilior",
    "populus spp": "Populus",
    "prunus cerasifera 'atropurpurea'": "Prunus cerasifera",
    "prunus cerasifera 'krauter vesuvius'": "Prunus cerasifera",
    "prunus cerasifera 'thundercloud'": "Prunus cerasifera",
    "prunus domestica 'green gage'": "Greengage",
    "prunus domestica 'mariposa'": "Prunus domestica",
    "prunus domestica 'santa rosa'": "Prunus domestica",
    "prunus ilicifoia": "Prunus ilicifolia",
    "prunus persica nectarina": "Nectarine",
    "prunus sargentii 'columnaris'": "Prunus sargentii",
    "prunus serrulata 'akebono'": "Prunus × yedoensis",
    "prunus serrulata 'amanagawa'": "Prunus serrulata",
    "prunus serrulata 'double pink weeping'": "Prunus serrulata",
    "prunus serrulata 'mt. fuji'": "Prunus serrulata",
    "prunus serrulata 'royal burgundy'": "Prunus serrulata",
    "prunus spp 'purpurea'": "Prunus cerasifera",
    "prunus spp": "Prunus",
    "prunus subhirtella 'autumnalis'": "Prunus subhirtella",
    "prunus x 'amanogawa'": "Prunus serrulata",
    "prunus x yedoensis 'akebono'": "Prunus × yedoensis",
    "punica granatum 'wonderfu": "Pomegranate",
    "pyracantha 'santa cruz'": "Pyracantha",
    "pyrus calleryana 'aristocrat'": "Pyrus calleryana",
    "pyrus calleryana 'bradford'": "Pyrus calleryana",
    "pyrus calleryana 'capital'": "Pyrus calleryana",
    "pyrus calleryana 'chanticleer'": "Pyrus calleryana",
    "pyrus calleryana 'cleveland'": "Pyrus calleryana",
    "pyrus calleryana 'new bradford'": "Pyrus calleryana",
    "pyrus calleryana 'redspire'": "Pyrus calleryana",
    "pyrus pyrifolia '20th century'": "Pyrus pyrifolia",
    "pyrus pyrifolia 'sainseiki'": "Pyrus pyrifolia",
    "pyrus spp": "Pyrus",
    "pyrus x 'bartlett'": "Williams pear",
    "quercus frainetto 'trump'": "Quercus frainetto",
    "quercus keloggii": "Quercus kelloggii",
    "quercus spp": "Oak",
    "rhamnus alaternus 'john edwards'": "Rhamnus alaternus",
    "rhaphiolepis majestic beauty": "Rhaphiolepis",
    "robinia pseudoacacia 'umbraculifera'": "Robinia pseudoacacia",
    "robinia x ambigua 'idahoensis'": "Robinia × ambigua",
    "robinia x ambigua 'purple robe'": "Robinia × ambigua",
    "robinia x ambigua": "Robinia × ambigua",
    "salix matsudana 'tortuosa'": "Salix matsudana",
    "salix spp": "Willow",
    "sambucus species": "Sambucus",
    "schefflera species": "Schefflera",
    "solanum rantonnetti": "Lycianthes rantonnetii",
    "sophora japonica 'regent'": "Styphnolobium japonicum",
    "syagrus romanzoffianum": "Syagrus romanzoffiana",
    "thuja occidentalis 'emerald'": "Thuja occidentalis",
    "tibochina urvilleana": "Tibouchina urvilleana",
    "tilia americana 'redmond'": "Tilia americana",
    "tilia spp": "Tilia",
    "tree(s)": "Tree",
    "tristaniopsis laurina 'elegant'": "Tristaniopsis laurina",
    "ulmus carpinifolia 'frontier'": "Ulmus",
    "ulmus parvifolia 'athena'": "Ulmus parvifolia",
    "ulmus propinqua 'emerald sunshine'": "Ulmus propinqua",
    "ulmus spp": "Elm",
    "viburnum odoratissimum var. awabuki": "Viburnum odoratissimum",
    "yucca spp": "Yucca",
    "zelkova serrata 'village green'": "Zelkova serrata",
    "patanus racemosa": "Platanus racemosa",
    "x chiranthofremontia lenzii": "× Chiranthofremontia",
    "brahea brandegeei": "Brahea",
    "caryota maxima 'himalaya'": "Caryota maxima",
    "citrus × limon 'lisbon'": "Lemon",
    "citrus × meyeri 'improved'": "Meyer lemon",
    "cornus nuttallii x florida 'eddie's white wonder'": "Cornus nuttallii",
    "dypsis cabadae": "Dypsis",
    "eucalyptus simmondsi": "Eucalyptus simmondsii",
    "ficus carica 'black mission'": "Common fig",
    "ficus carica 'brown turkey'": "Common fig",
    "fraxinus holotricha": "Fraxinus",
    "fraxinus x moraine": "Fraxinus americana",
    "leptospermum scoparium 'helene strybing'": "Leptospermum scoparium",
    "magnolia x foggii 'jack fogg'": "Magnolia × foggii",
    "pyracantha 'santa cruz'": "Pyracantha",
    "robinia x ambigua 'idahoensis'": "Robinia",
    "robinia x ambigua 'purple robe'": "Robinia",
    "robinia x ambigua": "Robinia × ambigua",
    "x chiranthofremontia lenzii": "× Chiranthofremontia",
    "betula alleghaniensis - yellow birch": "Betula alleghaniensis",
}

# ── Pydantic model ─────────────────────────────────────────────────────────────

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
        )
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






# ── External data sources ───────────────────────────────────────────────────────

HEADERS = {"User-Agent": "sf-tree-enrichment/1.0 (github.com/sf-tree-reporting)"}


@dataclass
class SourceTexts:
    wikipedia: str | None
    powo: str | None
    gbif: str | None
    selectree: str | None


_ECOREGION_CACHE: list[EcoregionReference] | None = None


def parse_scientific_name(q_species: str) -> str:
    return q_species.split("::")[0].strip()


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


def load_ecoregion_references() -> list[EcoregionReference]:
    global _ECOREGION_CACHE
    if _ECOREGION_CACHE is not None:
        return _ECOREGION_CACHE

    rows: list[tuple[int, str, str | None, str | None]] = []

    conn = duckdb.connect()
    try:
        try:
            rows = conn.execute(
                """
                SELECT
                    ecoregion_id,
                    ecoregion_name,
                    realm,
                    biome
                FROM read_parquet(?)
                WHERE ecoregion_id IS NOT NULL
                ORDER BY ecoregion_id
                """,
                [REMOTE_ECOREGION_PARQUET],
            ).fetchall()
        except Exception:
            rows = []
    finally:
        conn.close()

    if not rows:
        try:
            response = requests.get(
                LAYER_QUERY_URL,
                params={
                    "where": "ECO_ID IS NOT NULL AND ECO_ID > 0",
                    "outFields": "ECO_ID,ECO_NAME,REALM,BIOME_NAME",
                    "returnGeometry": "false",
                    "orderByFields": "ECO_ID",
                    "f": "json",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            rows = [
                (
                    int(feature["attributes"]["ECO_ID"]),
                    feature["attributes"]["ECO_NAME"],
                    feature["attributes"].get("REALM"),
                    feature["attributes"].get("BIOME_NAME"),
                )
                for feature in payload.get("features", [])
                if feature.get("attributes", {}).get("ECO_ID") is not None
            ]
        except Exception:
            rows = []

    _ECOREGION_CACHE = [
        make_ecoregion_reference(
            ecoregion_id=ecoregion_id,
            ecoregion_name=ecoregion_name,
            realm=realm,
            biome=biome,
        )
        for ecoregion_id, ecoregion_name, realm, biome in rows
    ]
    return _ECOREGION_CACHE


def build_native_range_evidence(texts: SourceTexts, reference_text: str) -> str:
    return build_native_range_evidence_for_entries(
        [
            ("Wikipedia", texts.wikipedia),
            ("POWO", texts.powo),
            ("GBIF", texts.gbif),
            ("SelecTree", texts.selectree),
        ],
        reference_text,
    )


def select_ecoregion_candidates(native_range_evidence: str, limit: int = 40) -> list[EcoregionReference]:
    return select_ecoregion_candidates_from_evidence(
        native_range_evidence,
        load_ecoregion_references(),
        limit=limit,
    )


def format_ecoregion_candidates(candidates: list[EcoregionReference]) -> str:
    if not candidates:
        return "No shortlist available. Return an empty list unless the evidence is exceptionally clear."
    return "\n".join(
        f"- {candidate.ecoregion_id} | {candidate.ecoregion_name} | realm={candidate.realm or 'unknown'} | biome={candidate.biome or 'unknown'}"
        for candidate in candidates
    )


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

MONTH_NAME_TO_NUM = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

SEASON_MONTHS = {
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "autumn": [9, 10, 11],
    "fall": [9, 10, 11],
    "winter": [12, 1, 2],
}


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


def normalize_powo_query_name(scientific_name: str) -> str:
    return normalize_enrichment_search_name(scientific_name)


def fetch_wikipedia_text(scientific_name: str) -> str | None:
    query_name = normalize_enrichment_search_name(scientific_name)
    slug = query_name.replace(" ", "_")
    summary_extract: str | None = None

    # REST summary endpoint — concise intro paragraph, best for LLM context
    r = requests.get(
        f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}",
        headers=HEADERS,
        timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        extract = data.get("extract", "")
        if extract:
            summary_extract = extract

    # MediaWiki API — fuller intro section, handles redirects.
    # Prefer this when available because it is usually richer than REST summary.
    r = requests.get(
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
    if r.status_code == 200:
        pages = r.json().get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("pageid", -1) != -1:
                extract = page.get("extract", "")
                if extract:
                    if summary_extract and len(summary_extract) > len(extract):
                        return summary_extract
                    return extract

    return summary_extract


def fetch_powo_text(scientific_name: str) -> str | None:
    """Fetch descriptive text from Plants of the World Online (POWO / Kew).

    POWO curates the World Checklist of Vascular Plants and provides
    habitat, morphology, and distribution descriptions.
    """
    try:
        powo_query_name = normalize_powo_query_name(scientific_name)
        r = requests.get(
            "https://powo.science.kew.org/api/2/search",
            params={"q": powo_query_name, "f": "species_f"},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results", [])
        if not results:
            return None
        top = results[0]
        matched_fq_id = top.get("fqId")
        if not matched_fq_id:
            return None
        accepted_ref = top.get("synonymOf") if not top.get("accepted", True) else None
        fq_id = accepted_ref.get("fqId") if isinstance(accepted_ref, dict) and accepted_ref.get("fqId") else matched_fq_id

        parts = []
        if powo_query_name != scientific_name:
            parts.append(f"POWO search query normalized to: {powo_query_name}")
        if top.get("name"):
            parts.append(f"POWO matched name: {top['name']}")
        if top.get("author"):
            parts.append(f"POWO author: {top['author']}")
        if top.get("family"):
            parts.append(f"POWO family: {top['family']}")
        if accepted_ref:
            accepted_name = accepted_ref.get("name")
            accepted_author = accepted_ref.get("author")
            if accepted_name:
                parts.append(f"POWO accepted name via synonym: {accepted_name}")
            if accepted_author:
                parts.append(f"POWO accepted author via synonym: {accepted_author}")
        snippet = top.get("snippet")
        if isinstance(snippet, str) and snippet.strip():
            snippet_text = snippet.replace("<b>", "").replace("</b>", "")
            parts.append(f"POWO snippet: {snippet_text}")

        r2 = requests.get(
            f"https://powo.science.kew.org/api/2/taxon/{fq_id}",
            params={"fields": "descriptions,distribution"},
            headers=HEADERS,
            timeout=10,
        )
        if r2.status_code != 200:
            return None
        data = r2.json()
        accepted_data = data.get("accepted")
        if (
            data.get("taxonomicStatus")
            and "synonym" in str(data.get("taxonomicStatus")).lower()
            and isinstance(accepted_data, dict)
            and accepted_data.get("fqId")
            and accepted_data.get("fqId") != fq_id
        ):
            accepted_fq_id = accepted_data["fqId"]
            r3 = requests.get(
                f"https://powo.science.kew.org/api/2/taxon/{accepted_fq_id}",
                params={"fields": "descriptions,distribution"},
                headers=HEADERS,
                timeout=10,
            )
            if r3.status_code == 200:
                data = r3.json()
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

        # Include structured distribution evidence whenever available.
        dist = data.get("distribution", {})
        if isinstance(dist, dict):
            for bucket in ("natives", "introduced", "extinct", "uncertain"):
                entries = dist.get(bucket, [])
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
                        detail_bits.append(
                            "path=" + " > ".join(str(node) for node in entry["locationTree"][:10])
                        )
                    if entry.get("establishment"):
                        detail_bits.append(f"establishment={entry['establishment']}")
                    if detail_bits:
                        detailed_entries.append("; ".join(detail_bits))
                if detailed_entries:
                    parts.append(f"Distribution {bucket} detail: {' | '.join(detailed_entries)}")

        return "\n".join(parts) if parts else None
    except Exception:
        return None


def fetch_gbif_text(scientific_name: str) -> str | None:
    """Fetch taxonomic context from GBIF species match API.

    GBIF provides canonical names and family/order classification,
    useful for resolving synonyms and taxonomy.
    """
    try:
        query_name = normalize_enrichment_search_name(scientific_name)
        r = requests.get(
            "https://api.gbif.org/v1/species/match",
            params={"name": query_name, "verbose": "true"},
            headers=HEADERS,
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        usage_key = data.get("usageKey") or data.get("speciesKey")
        if not usage_key:
            return None

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
            alt_names = [a.get("scientificName") for a in alternatives[:3] if isinstance(a, dict) and a.get("scientificName")]
            if alt_names:
                parts.append(f"Alternative matches: {' | '.join(alt_names)}")

        try:
            vn = requests.get(
                f"https://api.gbif.org/v1/species/{usage_key}/vernacularNames",
                params={"limit": 20},
                headers=HEADERS,
                timeout=10,
            )
            if vn.status_code == 200:
                vn_data = vn.json()
                rows = vn_data.get("results", []) if isinstance(vn_data, dict) else []
                names = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    lang = row.get("language")
                    name = row.get("vernacularName")
                    if not name:
                        continue
                    if lang and str(lang).lower().startswith("en"):
                        names.append(name)
                if names:
                    uniq = []
                    seen = set()
                    for n in names:
                        k = n.strip().lower()
                        if k in seen:
                            continue
                        seen.add(k)
                        uniq.append(n.strip())
                    if uniq:
                        parts.append(f"GBIF vernacular names (en): {', '.join(uniq[:8])}")
        except Exception:
            pass

        return "\n".join(parts) if parts else None
    except Exception:
        return None


def fetch_selectree_text(scientific_name: str) -> str | None:
    """Fetch rich structured tree metadata from SelecTree APIs."""
    def clean_value(value):
        if value is None:
            return None
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        text = str(value)
        text = html.unescape(text)
        text = re.sub(r"<[^>]+>", "", text)
        return text.strip()

    try:
        query_name = normalize_enrichment_search_name(scientific_name)
        search_resp = requests.get(
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
        if search_resp.status_code != 200:
            return None

        payload = search_resp.json()
        rows = payload.get("pageResults", []) if isinstance(payload, dict) else []
        if not rows:
            return None

        selected = rows[0]
        target = query_name.strip().lower()
        for row in rows:
            accepted = str(row.get("accepted_scientific") or "").strip().lower()
            raw_name = str(row.get("name_concat") or "").strip().lower()
            if accepted == target or raw_name == target:
                selected = row
                break

        tree_id = selected.get("tree_id")
        if not tree_id:
            return None

        parts = [
            f"SelecTree tree_id: {tree_id}",
            f"SelecTree accepted scientific: {clean_value(selected.get('accepted_scientific') or selected.get('name_concat') or scientific_name)}",
        ]
        if query_name != scientific_name:
            parts.insert(0, f"SelecTree search query normalized to: {query_name}")

        if selected.get("common"):
            parts.append(f"SelecTree common name: {clean_value(selected['common'])}")
        if selected.get("family"):
            parts.append(f"SelecTree family: {clean_value(selected['family'])}")
        if selected.get("height_high"):
            parts.append(f"SelecTree reported max height: {selected['height_high']} ft")

        detail_resp = requests.get(
            f"https://selectree.calpoly.edu/api/tree/detail/{tree_id}",
            headers=HEADERS,
            timeout=12,
        )
        if detail_resp.status_code == 200:
            detail = detail_resp.json()
            if isinstance(detail, dict):
                if detail.get("memo"):
                    parts.append(f"SelecTree memo: {clean_value(detail['memo'])}")
                if detail.get("native_range"):
                    parts.append(f"SelecTree native range: {clean_value(detail['native_range'])}")
                if detail.get("foliage_type"):
                    parts.append(f"SelecTree foliage type: {clean_value(detail['foliage_type'])}")
                if detail.get("growth_rate_low") or detail.get("growth_rate_high"):
                    parts.append(
                        f"SelecTree growth rate range: {detail.get('growth_rate_low')} to {detail.get('growth_rate_high')}"
                    )
                if detail.get("width_low") or detail.get("width_high"):
                    parts.append(f"SelecTree canopy width range: {detail.get('width_low')} to {detail.get('width_high')} ft")
                if detail.get("height_low") or detail.get("height_high"):
                    parts.append(f"SelecTree height range: {detail.get('height_low')} to {detail.get('height_high')} ft")
                if detail.get("water_use"):
                    parts.append(f"SelecTree water use: {clean_value(detail['water_use'])}")
                if detail.get("flower_time"):
                    parts.append(f"SelecTree flower time: {clean_value(detail['flower_time'])}")
                if detail.get("flower_showiness"):
                    parts.append(f"SelecTree flower showiness: {clean_value(detail['flower_showiness'])}")
                if detail.get("fruiting_time"):
                    parts.append(f"SelecTree fruiting time: {clean_value(detail['fruiting_time'])}")
                if detail.get("attracts_wildlife"):
                    parts.append(f"SelecTree attracts wildlife: {clean_value(detail['attracts_wildlife'])}")
                if detail.get("disease_resistant"):
                    parts.append(f"SelecTree disease resistant: {clean_value(detail['disease_resistant'])}")
                if detail.get("pest_resistant"):
                    parts.append(f"SelecTree pest resistant: {clean_value(detail['pest_resistant'])}")

                # Common names from detail payload
                primary_common = detail.get("primary_common") or {}
                if isinstance(primary_common, dict) and primary_common.get("common"):
                    parts.append(f"SelecTree primary common: {clean_value(primary_common['common'])}")
                other_common = detail.get("other_common") or []
                if isinstance(other_common, list):
                    names = [clean_value(row.get("common")) for row in other_common if isinstance(row, dict) and row.get("common")]
                    if names:
                        parts.append(f"SelecTree other common names: {', '.join(names[:10])}")

        return "\n".join(parts)
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


def build_enrichment_prompt(scientific_name: str, wiki_name: str, reference_text: str) -> str:
    return (
        "You are enriching tree data for an urban forestry dataset.\n"
        "Extract structured information about this tree species from the reference text below.\n"
        "Be conservative with numeric estimates - use None if the sources do not clearly state a value.\n"
        "For mature height and canopy spread, extract min and max numeric values plus the original unit instead of converting units yourself.\n"
        "For growth rate, prefer annual numeric growth increments with units; only use the qualitative slow/moderate/fast field when the source explicitly uses those words.\n"
        "For bloom_months, return month numbers 1-12 rather than season text.\n"
        "For description, write a brief 1-3 sentence summary covering appearance, ecology, and urban planting relevance.\n\n"
        f"Species: {scientific_name}\n\n"
        f"Wikipedia lookup: {wiki_name}\n\n"
        f"Reference text:\n{reference_text}"
    )


def parse_enrichment_from_text(
    scientific_name: str,
    wiki_name: str,
    texts: SourceTexts,
    client,
    print_full_context: bool = False,
) -> TreeEnrichment | None:
    reference_text = build_reference_text(texts)
    if not reference_text:
        return None

    if print_full_context:
        print("[debug] BEGIN LLM REFERENCE TEXT", file=sys.stderr)
        print(reference_text, file=sys.stderr)
        print("[debug] END LLM REFERENCE TEXT", file=sys.stderr)

    try:
        return client.chat.completions.create(
            response_model=TreeEnrichment,
            messages=[{
                "role": "user",
                "content": build_enrichment_prompt(scientific_name, wiki_name, reference_text),
            }],
        )
    except Exception as e:
        print(f"  [error] instructor failed for {scientific_name!r}: {e}", file=sys.stderr)
        return None


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
        "For description, write a brief 1-3 sentence summary covering appearance, ecology, and urban planting relevance.\n\n"
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
    ecoregion_candidates: list[EcoregionReference],
    print_full_context: bool = False,
) -> TreeEnrichment | None:
    reference_text = build_reference_text(texts)
    if not reference_text:
        return None
    native_range_evidence = build_native_range_evidence(texts, reference_text)

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
    except Exception as e:
        print(f"  [error] instructor failed for {scientific_name!r}: {e}", file=sys.stderr)
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


def run_standalone_debug(q_species: str, client) -> int:
    scientific_name = parse_scientific_name(q_species)
    if not scientific_name:
        print("[debug] empty species", file=sys.stderr)
        return 2

    wiki_name = map_wikipedia_lookup(scientific_name)
    print(f"[debug] input: {q_species}", file=sys.stderr)
    print(f"[debug] scientific_name: {scientific_name}", file=sys.stderr)
    print(f"[debug] wikipedia_lookup: {wiki_name}", file=sys.stderr)

    texts = gather_source_texts(scientific_name, wiki_name)
    labels = source_labels(texts)
    native_range_evidence = build_native_range_evidence(texts, build_reference_text(texts))
    ecoregion_candidates = select_ecoregion_candidates(native_range_evidence)
    if labels:
        print(f"[debug] sources: {', '.join(labels)}", file=sys.stderr)
    else:
        print("[debug] sources: none", file=sys.stderr)
    print(f"[debug] ecoregion shortlist count: {len(ecoregion_candidates)}", file=sys.stderr)

    debug_print_source_details(texts)

    reference_text = build_reference_text(texts)
    print(f"[debug] combined_reference_chars: {len(reference_text)}", file=sys.stderr)

    enrichment = parse_enrichment_from_text_v2(
        scientific_name,
        wiki_name,
        texts,
        client,
        ecoregion_candidates,
        print_full_context=True,
    )
    if enrichment is None:
        print("[debug] enrichment: none", file=sys.stderr)
        return 1

    is_complete, missing = compute_is_complete(enrichment)
    print("[debug] parsed enrichment JSON:", file=sys.stderr)
    print(enrichment.model_dump_json(indent=2), file=sys.stderr)
    if is_complete:
        print("[debug] completeness: complete", file=sys.stderr)
    else:
        print(f"[debug] completeness: incomplete; missing: {', '.join(missing)}", file=sys.stderr)
    return 0


# ── Enrichment ─────────────────────────────────────────────────────────────────

def enrich_species(q_species: str, client, print_full_context: bool = False) -> TreeEnrichment | None:
    scientific_name = parse_scientific_name(q_species)
    if not scientific_name:
        return None
    
    wiki_name = map_wikipedia_lookup(scientific_name)

    # Gather text from all available sources
    texts = gather_source_texts(scientific_name, wiki_name)
    labels = source_labels(texts)

    if not labels:
        print(
            f"  [skip] no content found for {scientific_name!r} (lookup: {wiki_name!r})",
            file=sys.stderr,
        )
        return None

    print(f"    [sources] {', '.join(labels)}", file=sys.stderr)
    native_range_evidence = build_native_range_evidence(texts, build_reference_text(texts))
    ecoregion_candidates = select_ecoregion_candidates(native_range_evidence)
    if ecoregion_candidates:
        print(f"    [ecoregions] shortlist={len(ecoregion_candidates)}", file=sys.stderr)
    return parse_enrichment_from_text_v2(
        scientific_name,
        wiki_name,
        texts,
        client,
        ecoregion_candidates,
        print_full_context=print_full_context,
    )


# ── Species list ───────────────────────────────────────────────────────────────

def get_all_species() -> list[str]:
    """Return all distinct species values from the tree dataset."""
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            f"""
            SELECT DISTINCT species
            FROM read_parquet(?)
            WHERE {SPECIES_EXCLUSION_SQL}
            ORDER BY species
            """,
            [TREE_INFO_PARQUET],
        ).fetchall()
        return [row[0] for row in rows if not should_skip_species(row[0])]
    finally:
        conn.close()


_NEW_COMPLETENESS_EXPR = """
    (common_names IS NOT NULL
     AND array_length(common_names) > 0
     AND is_evergreen IS NOT NULL
     AND mature_height_max_ft IS NOT NULL
     AND canopy_spread_max_ft IS NOT NULL
     AND growth_rate IS NOT NULL
     AND drought_tolerance IS NOT NULL
     AND tree_form IS NOT NULL)
""".strip()

SKIP_SPECIES = {'Scheduled Planting Site - Spring 2026', 'Vacant Unacceptable/Retired', 'Vacant site medium' }

def get_already_enriched(source: str = ENRICHMENT_PARQUET) -> set[str]:
    """Return the set of species names already present in *source* (local path or https URL)."""
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            """
            SELECT species
            FROM read_parquet(?)
            WHERE species IS NOT NULL
            """,
            [source],
        ).fetchall()
        return {row[0] for row in rows}.union(SKIP_SPECIES)
    except Exception:
        return set()
    finally:
        conn.close()


def load_existing_table(source: str) -> pa.Table | None:
    """Load the full enrichment parquet from *source*.

    Returns None only if the file does not exist (local path missing or GCS 404).
    Raises on any other error (schema mismatch, network failure, etc.).
    """
    conn = duckdb.connect()
    try:
        table = conn.execute(
            f"""
            SELECT
              species,
              genus,
              species_epithet,
              family,
              common_names,
              description,
              is_evergreen,
              mature_height_min_ft,
              mature_height_max_ft,
              canopy_spread_min_ft,
              canopy_spread_max_ft,
              growth_rate,
              lifespan_min_years,
              lifespan_max_years,
              drought_tolerance,
              water_needs,
              sun_exposure,
              soil_preferences,
              root_behavior,
              coastal_tolerance,
              salt_tolerance,
              pollution_tolerance,
              bloom_months,
              wildlife_value,
              fire_risk,
              tree_form,
              usda_zone_min,
              usda_zone_max,
              native_ecoregions,
              {_NEW_COMPLETENESS_EXPR} AS is_complete,
              enriched_at
            FROM read_parquet(?)
            """,
            [source],
        ).fetch_arrow_table()
    except Exception as exc:
        msg = str(exc)
        if (
            "No such file or directory" in msg
            or "does not exist" in msg
            or "404" in msg
            or "HTTP" in msg and "404" in msg
        ):
            return None
        raise RuntimeError(f"load_existing_table({source!r}) failed: {exc}") from exc
    finally:
        conn.close()

    # Drop any extra columns not in SCHEMA (e.g. legacy icon_rgba_b64/width/height)
    schema_names = {f.name for f in SCHEMA}
    extra = [name for name in table.schema.names if name not in schema_names]
    if extra:
        table = table.drop(extra)

    # Cast to canonical SCHEMA — normalizes list child field names (DuckDB emits 'l', PyArrow uses 'item')
    # and ensures all column types match exactly before concat.
    table = table.cast(SCHEMA)

    return table


def merge_with_existing(existing: pa.Table | None, new_rows: list[dict]) -> pa.Table:
    """Merge *new_rows* into *existing*, replacing any species that appear in new_rows."""
    new_table = build_table(new_rows)
    if existing is None or len(existing) == 0:
        return new_table
    re_processed = pa.array([row["species"] for row in new_rows], type=pa.string())
    keep_mask = pc.invert(pc.is_in(existing.column("species"), re_processed))
    return pa.concat_tables([existing.filter(keep_mask), new_table])


def upload_to_gcs(local_path: str, gcs_uri: str) -> None:
    """Copy *local_path* to *gcs_uri* using gsutil."""
    print(f"[info] uploading {local_path} → {gcs_uri}", file=sys.stderr)
    result = subprocess.run(
        ["gsutil", "cp", local_path, gcs_uri],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        print("[info] upload complete", file=sys.stderr)
    else:
        print(f"[error] gsutil upload failed:\n{result.stderr.strip()}", file=sys.stderr)


def compute_is_complete(enrichment: TreeEnrichment) -> tuple[bool, list[str]]:
    """Return (is_complete, missing_fields).

    is_complete is True only when every core Optional field is non-null.
    missing_fields lists the names of any fields that are still None/empty.
    """
    checks = [
        ("common_names",    bool(enrichment.common_names)),
        ("description",     bool(enrichment.description and enrichment.description.strip())),
        ("is_evergreen",    enrichment.is_evergreen      is not None),
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


# ── Arrow table ────────────────────────────────────────────────────────────────

SCHEMA = pa.schema([
    ("species",               pa.string()),
    ("genus",                 pa.string()),
    ("species_epithet",       pa.string()),
    ("family",                pa.string()),
    ("common_names",          pa.list_(pa.string())),
    ("description",           pa.string()),
    ("is_evergreen",          pa.bool_()),
    ("mature_height_min_ft",  pa.float32()),
    ("mature_height_max_ft",  pa.float32()),
    ("canopy_spread_min_ft",  pa.float32()),
    ("canopy_spread_max_ft",  pa.float32()),
    ("growth_rate",           pa.string()),
    ("lifespan_min_years",    pa.int32()),
    ("lifespan_max_years",    pa.int32()),
    ("drought_tolerance",     pa.string()),
    ("water_needs",           pa.string()),
    ("sun_exposure",          pa.list_(pa.string())),
    ("soil_preferences",      pa.list_(pa.string())),
    ("root_behavior",         pa.string()),
    ("coastal_tolerance",     pa.bool_()),
    ("salt_tolerance",        pa.bool_()),
    ("pollution_tolerance",   pa.string()),
    ("bloom_months",          pa.list_(pa.int32())),
    ("wildlife_value",        pa.string()),
    ("fire_risk",             pa.string()),
    ("tree_form",             pa.string()),
    ("usda_zone_min",         pa.int32()),
    ("usda_zone_max",         pa.int32()),
    ("native_ecoregions",     pa.list_(pa.int32())),
    ("is_complete",           pa.bool_()),
    ("enriched_at",           pa.timestamp("us", tz="UTC")),
])


def build_table(rows: list[dict]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--debug-species",
        dest="debug_species",
        help="Run standalone parsing debug for a single species, e.g. \"Abutilon hybridum\"",
    )
    parser.add_argument(
        "--print-llm-context",
        action="store_true",
        help="Print full concatenated source text sent to the LLM for each processed species.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Process at most N species. Writes checkpoints to --output and uploads "
            "to GCS at the end. Reads progress from the local file instead of remote."
        ),
    )
    parser.add_argument(
        "--output",
        default="tree_enrichment.parquet",
        metavar="PATH",
        help="Local parquet checkpoint file used with --limit (default: tree_enrichment.parquet).",
    )
    parser.add_argument(
        "--flush-every",
        dest="flush_every",
        type=int,
        default=10,
        metavar="N",
        help="Write a checkpoint to --output every N successfully enriched species (default: 10).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_INSTRUCTOR_MODEL,
        help=(
            "Instructor model to use for enrichment. Accepts either a full provider "
            f"model name or a Gemini shorthand (default: {DEFAULT_INSTRUCTOR_MODEL})."
        ),
    )
    parser.add_argument(
        "--vertex-project",
        dest="vertex_project",
        default=DEFAULT_VERTEX_PROJECT,
        help=f"Vertex AI project for instructor provider calls (default: {DEFAULT_VERTEX_PROJECT}).",
    )
    parser.add_argument(
        "--vertex-location",
        dest="vertex_location",
        default=DEFAULT_VERTEX_LOCATION,
        help=f"Vertex AI location for instructor provider calls (default: {DEFAULT_VERTEX_LOCATION}).",
    )
    args = parser.parse_args()

    normalized_model = normalize_instructor_model_name(args.model)
    print(
        f"[info] instructor model={normalized_model} | "
        f"vertex_project={args.vertex_project} | vertex_location={args.vertex_location}",
        file=sys.stderr,
    )
    client = create_instructor_client(
        normalized_model,
        args.vertex_project,
        args.vertex_location,
    )

    if args.debug_species:
        raise SystemExit(run_standalone_debug(args.debug_species, client))

    # In local-refresh mode, read progress from the local checkpoint if it exists;
    # otherwise fall back to the remote parquet.
    local_mode = args.limit is not None
    if local_mode and os.path.exists(args.output):
        enrichment_source = args.output
        print(f"[info] local mode: reading progress from {args.output}", file=sys.stderr)
    else:
        enrichment_source = ENRICHMENT_PARQUET

    already_enriched = get_already_enriched(enrichment_source)
    all_species = get_all_species()
    to_process = [s for s in all_species if parse_scientific_name(s) not in already_enriched]
    if local_mode:
        to_process = to_process[:args.limit]

    print(
        f"[info] {len(all_species)} total species | "
        f"{len(already_enriched)} already enriched | "
        f"{len(to_process)} to process",
        file=sys.stderr,
    )

    # Pre-load existing table once so every checkpoint flush is cheap.
    existing_table = load_existing_table(enrichment_source) if already_enriched else None
    if already_enriched and (existing_table is None or len(existing_table) == 0):
        raise RuntimeError(
            f"existing_table is empty/None but get_already_enriched() returned "
            f"{len(already_enriched)} species from {enrichment_source!r}. "
            f"load_existing_table() silently failed — check schema compatibility or URL reachability."
        )

    new_rows: list[dict] = []
    counter = 0
    for q_species in to_process:
        if not q_species:
            continue
        status = "re-enrich" if parse_scientific_name(q_species) in already_enriched else "new"
        print(f"{counter:03d}/{len(to_process):03d}  [{status}] {q_species}", file=sys.stderr)
        enrichment = enrich_species(q_species, client, print_full_context=args.print_llm_context)
        if enrichment is None:
            continue
        counter +=1
        is_complete, missing = compute_is_complete(enrichment)
        if missing:
            print(f"    [incomplete] missing: {', '.join(missing)}", file=sys.stderr)
        else:
            print(f"    [complete]", file=sys.stderr)
        scientific_name = parse_scientific_name(q_species)
        genus, species_epithet = split_scientific_parts(scientific_name)
        lifespan_min_years, lifespan_max_years = parse_lifespan_range(enrichment.lifespan_years)
        mature_height_min_ft, mature_height_max_ft = convert_length_range_to_feet(
            enrichment.mature_height_min_value,
            enrichment.mature_height_max_value,
            enrichment.mature_height_unit,
        )
        canopy_spread_min_ft, canopy_spread_max_ft = convert_length_range_to_feet(
            enrichment.canopy_spread_min_value,
            enrichment.canopy_spread_max_value,
            enrichment.canopy_spread_unit,
        )
        bloom_months = normalize_bloom_months(enrichment.bloom_months)
        normalized_growth_rate = normalize_growth_rate(
            enrichment.growth_rate_min_value,
            enrichment.growth_rate_max_value,
            enrichment.growth_rate_unit,
            enrichment.growth_rate,
        )
        new_rows.append({
            "species":              scientific_name,
            "genus":                genus,
            "species_epithet":      species_epithet,
            "family":               None,
            "common_names":         enrichment.common_names or None,
            "description":          enrichment.description.strip() if enrichment.description else None,
            "is_evergreen":         enrichment.is_evergreen,
            "mature_height_min_ft": mature_height_min_ft,
            "mature_height_max_ft": mature_height_max_ft,
            "canopy_spread_min_ft": canopy_spread_min_ft,
            "canopy_spread_max_ft": canopy_spread_max_ft,
            "growth_rate":          normalized_growth_rate,
            "lifespan_min_years":   lifespan_min_years,
            "lifespan_max_years":   lifespan_max_years,
            "drought_tolerance":    enrichment.drought_tolerance,
            "water_needs":          enrichment.water_needs,
            "sun_exposure":         enrichment.sun_exposure or None,
            "soil_preferences":     enrichment.soil_preferences or None,
            "root_behavior":        enrichment.root_behavior,
            "coastal_tolerance":    enrichment.coastal_tolerance,
            "salt_tolerance":       enrichment.salt_tolerance,
            "pollution_tolerance":  enrichment.pollution_tolerance,
            "bloom_months":         bloom_months,
            "wildlife_value":       enrichment.wildlife_value,
            "fire_risk":            enrichment.fire_risk,
            "tree_form":            map_tree_form(enrichment.tree_form),
            "usda_zone_min":        enrichment.usda_zone_min,
            "usda_zone_max":        enrichment.usda_zone_max,
            "native_ecoregions":    sorted(set(enrichment.native_ecoregions)) or None,
            "is_complete":          is_complete,
            # "icon_rgba_b64":        to_raw_rgba_b64(icon),
            # "icon_width":           ICON_SIZE,
            # "icon_height":          ICON_SIZE,
            "enriched_at":          datetime.now(tz=timezone.utc),
        })

        # Periodic checkpoint flush
        if local_mode and len(new_rows) % args.flush_every == 0:
            checkpoint = merge_with_existing(existing_table, new_rows)
            pq.write_table(checkpoint, args.output)
            print(
                f"  [checkpoint] {len(new_rows)} new rows written to {args.output}",
                file=sys.stderr,
            )

    merged = merge_with_existing(existing_table, new_rows)

    if existing_table is not None and len(existing_table) > 0 and new_rows:
        new_species_set = {row["species"] for row in new_rows}
        overlap = sum(1 for s in existing_table.column("species").to_pylist() if s in new_species_set)
        expected = len(existing_table) - overlap + len(new_rows)
        if len(merged) != expected:
            raise RuntimeError(
                f"Merge validation failed: expected {expected} rows "
                f"({len(existing_table)} existing − {overlap} re-processed + {len(new_rows)} new), "
                f"got {len(merged)}. "
                f"Existing rows were likely dropped due to a silent load failure."
            )

    if local_mode:
        pq.write_table(merged, args.output)
        print(f"[info] wrote {len(merged)} rows to {args.output}", file=sys.stderr)
        upload_to_gcs(args.output, ENRICHMENT_GCS_URI)
    else:
        emit(merged)
