"""Resolve a Korean inventory's vernacular name to an accepted binomial.

NOT a uv inline script -- a regular importable module, like `shared.ingest`.

Seoul publishes its street trees under the Korean vernacular name and nothing
else: `은행나무`, `느티나무`, `이팝나무`, 73 distinct values over 287,636 rows
of the 2026 street-tree file, with no binomial column anywhere.  `species` is
the join key into the enrichment table, so without this the whole city arrives
labelled `Unknown`.

The shape is `shared.species.japanese`'s, deliberately: a curated dict, a key
normaliser, and a lookup that returns `None` rather than guessing.  It is a
separate module because the keys are Hangul and normalise by their own rule.

Usage:

    from shared.species.korean import species_from_korean_name

    species_from_korean_name("은행나무")         # 'Ginkgo biloba'
    species_from_korean_name("은행나무 암나무")  # 'Ginkgo biloba' -- a female ginkgo
    species_from_korean_name("벚나무류")         # 'Prunus' -- a genus is an answer
    species_from_korean_name("기타")             # None -- "other"
    is_korean_non_tree("공분")                   # True -- an empty tree pit

**GBIF drafted this table and WCVP adjudicated it; neither was trusted alone.**
GBIF's backbone carries Korean vernacular names, and an exact Korean-language
match resolved 37 of the 73 values (November 2026).  It is a drafting aid, not
the authority, and three of its answers were wrong in ways that do not announce
themselves:

* `자작나무` came back *Betula pendula*.  `자작나무` is the Korean and
  Manchurian white birch, *Betula platyphylla* (var. *japonica*); *B. pendula*
  is `유럽자작나무`/`은자작나무`.  Published as `Betula platyphylla`, the same
  deliberate synonym `shared.species.japanese` keeps for `シラカバ`, so the two
  cities share one enrichment row.
* `꽃사과나무` came back *Malus floribunda*.  `꽃사과` is "flowering crabapple",
  a category; *M. floribunda* is one of the many species sold under it.  The
  genus, *Malus*, is what the publisher wrote.
* `목련` came back *Magnolia kobus*.  That is the name's botanical reading, but
  in ordinary and nursery use `목련` is the category "magnolia" -- Seoul's
  street magnolias are overwhelmingly `백목련`, *M. denudata* -- so it
  resolves to the genus, as `プラタナス` does in the Japanese table.

The other 36 were filled by hand from the Korean standard plant names
(국가표준식물목록) and every value was put to WCVP the way `species_audit.py`
does it.  All are accepted there except three, which follow the repo's rule
that the published table wins over a newer name (the argument is in
`shared.species.japanese`): `Betula platyphylla` (above), `Picea glauca` (WCVP
files it under *Picea laxa*; the enrichment table has carried *P. glauca* for
years) and `Populus x tomentiglandulosa`, an artificial hybrid WCVP records as
such.  One value took the accepted name where a familiar one exists:
`팥배나무` is *Micromeles alnifolia*, WCVP's accepted name for what is often
written *Sorbus alnifolia* (a synonym there).

**A genus is an answer.**  A value ending `류` ("-kind") says so outright --
`벚나무류` is 31,550 trees recorded as "cherry", `참나무류` is "oak",
`잣나무류` is "white pines" -- and those resolve to the genus.  So do
`포플러` (poplar), `오동나무` (the paulownias; the standard name points at
*P. coreana*, whose standing is doubtful) and the two category names above.

**Forms and cultivars resolve to their species.**  `반송` (the multi-stemmed
umbrella form) and `금강송` (the Geumgang regional form) are *Pinus
densiflora*; `청단풍나무` (green-leaved) and `공작단풍` (the cut-leaf
Dissectum group) are *Acer palmatum*; `선주목` (upright yew) is *Taxus
cuspidata*; `코니카가문비` is *Picea glauca* 'Conica'.  The two that are named
selections keep their cultivar on the row through `cultivar_from_korean_name`.

**One judgement call is worth naming.**  `왕벚나무` is published as
`Prunus x yedoensis`, the Somei-yoshino cherry that Korean street plantings
overwhelmingly are, rather than the native Jeju king cherry (*Prunus x
nudiflora*), which the Korean name also covers in current botany.  The
street-tree reading is the safe one; the alternative would assert a rare
native taxon for 1,416 street trees.

**Deliberately left unresolved** (publish as `Unknown`):

* `기타` (1,367), "other" -- nothing to resolve;
* `보리수` (10), which names three unrelated trees in ordinary use -- the
  autumn olive *Elaeagnus umbellata* (`보리수나무`), the temple linden *Tilia
  miqueliana* (`보리자나무`) and the bodhi fig *Ficus religiosa*;
* `댕강나무` (4): the standard name is the Korean endemic *Zabelia
  tyaihyoni*, but a street planting of "댕강나무" is far more likely the
  glossy abelia `꽃댕강나무`, *Abelia x grandiflora*.  Four trees are not worth
  a guess either way.

**Two values are not trees at all** and `is_korean_non_tree` says so, for the
ingest to drop with a count: `공분` (5,881), the Seoul Metropolitan
Government's own term for a street-tree pit with no tree in it (its press
releases gloss it as `가로수가 비어있는 곳`, "where the street tree is
missing"), and `(수동)폭염그늘막`, a manually operated heat-wave shade canopy.
The shared `is_not_a_tree` markers are English and French and cannot see
either, which is why the decision lives here rather than being passed through.

`tests/test_korean_species.py` pins the mechanical half -- every value is a
name `sanitize_species` keeps as written, every key is in normalised form, no
value is also a key, no value is a sentinel.  The taxonomy is reviewed by
reading the file.
"""

from __future__ import annotations

import unicodedata

from shared.ingest import form_sentinel_for, is_not_a_tree


def korean_name_key(value: str | None) -> str:
    """A published Korean name reduced to the form the table uses.

    NFKC-normalised (which also recomposes any decomposed Hangul jamo) and
    stripped of every space, so `은행나무 암나무` and `은행나무암나무` are
    one key.  Returns `""` for a value with nothing left in it.
    """
    if value is None:
        return ""
    return "".join(unicodedata.normalize("NFKC", str(value)).split())


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------
#
# Keys are `korean_name_key` output; values are names `sanitize_species` keeps
# as written -- accepted in WCVP (or kept under the published-table rule
# above), ASCII hybrid mark, genus capitalised, no rank below species.  Sorted
# by value, because that is how it was checked.  A few keys are spellings the
# 2026 file does not use (`튤립나무`, `박태기나무`, `왕버들`) but the standard
# name does; they cost nothing and spare a re-curation when the next file
# tidies its spelling.

KOREAN_SPECIES: dict[str, str] = {
    "구상나무": "Abies koreana",
    "중국단풍": "Acer buergerianum",
    "단풍나무": "Acer palmatum",
    "청단풍나무": "Acer palmatum",
    "공작단풍": "Acer palmatum",
    "은단풍": "Acer saccharinum",
    "복자기": "Acer triflorum",
    "칠엽수": "Aesculus turbinata",
    "가죽나무": "Ailanthus altissima",
    "가중나무": "Ailanthus altissima",
    "자귀나무": "Albizia julibrissin",
    "자작나무": "Betula platyphylla",
    "히말라야시다": "Cedrus deodara",
    "팽나무": "Celtis sinensis",
    "계수나무": "Cercidiphyllum japonicum",
    "박태기": "Cercis chinensis",
    "박태기나무": "Cercis chinensis",
    "이팝나무": "Chionanthus retusus",
    "층층나무": "Cornus controversa",
    "산딸나무": "Cornus kousa",
    "산수유": "Cornus officinalis",
    "산사나무": "Crataegus pinnatifida",
    "감나무": "Diospyros kaki",
    "은행나무": "Ginkgo biloba",
    "은행나무암나무": "Ginkgo biloba",  # 은행나무 암나무, a female ginkgo
    "칠자화": "Heptacodium miconioides",
    "무궁화": "Hibiscus syriacus",
    "낙상홍": "Ilex serrata",
    "향나무": "Juniperus chinensis",
    "배롱나무": "Lagerstroemia indica",
    "튜울립나무": "Liriodendron tulipifera",
    "튤립나무": "Liriodendron tulipifera",
    "목련": "Magnolia",
    "꽃사과나무": "Malus",
    "야광나무": "Malus baccata",
    "아그배나무": "Malus toringo",
    "메타세쿼이아": "Metasequoia glyptostroboides",
    "팥배나무": "Micromeles alnifolia",
    "오동나무": "Paulownia",
    "코니카가문비": "Picea glauca",
    "잣나무류": "Pinus",
    "소나무": "Pinus densiflora",
    "반송": "Pinus densiflora",
    "금강송": "Pinus densiflora",
    "양버즘나무": "Platanus occidentalis",
    "측백나무": "Platycladus orientalis",
    "포플러": "Populus",
    "미루나무": "Populus deltoides",
    "은사시나무": "Populus x tomentiglandulosa",
    "벚나무류": "Prunus",
    "살구나무": "Prunus armeniaca",
    "매화나무": "Prunus mume",
    "귀룽나무": "Prunus padus",
    "꽃복숭아": "Prunus persica",
    "복사나무": "Prunus persica",
    "자두나무": "Prunus salicina",
    "왕벚나무": "Prunus x yedoensis",
    "모과나무": "Pseudocydonia sinensis",
    "참나무류": "Quercus",
    "대왕참나무": "Quercus palustris",
    "수양버들": "Salix babylonica",
    "왕버들": "Salix chaenomeloides",
    "왕버들나무": "Salix chaenomeloides",
    "마가목": "Sorbus commixta",
    "회화나무": "Styphnolobium japonicum",
    "때죽나무": "Styrax japonicus",
    "라일락": "Syringa vulgaris",
    "선주목": "Taxus cuspidata",
    "주목": "Taxus cuspidata",
    "느릅나무": "Ulmus davidiana",
    "비술나무": "Ulmus pumila",
    "느티나무": "Zelkova serrata",
    "대추나무": "Ziziphus jujuba",
}

# Named selections whose taxon is in `KOREAN_SPECIES`; the cultivar rides on
# the row's `cultivar` column, as `extract_cultivar` would have put it.
KOREAN_CULTIVARS: dict[str, str] = {
    "공작단풍": "Dissectum",
    "코니카가문비": "Conica",
}

# Values that record something other than a tree.  See the module docstring.
KOREAN_NON_TREES: frozenset[str] = frozenset({
    "공분",              # an empty street-tree pit
    "(수동)폭염그늘막",  # a manually operated heat-wave shade canopy
})


def is_korean_non_tree(value: str | None) -> bool:
    """True when a published value records an empty pit or a structure."""
    return korean_name_key(value) in KOREAN_NON_TREES


def cultivar_from_korean_name(value: str | None) -> str | None:
    """The cultivar a Korean name implies, or ``None``."""
    return KOREAN_CULTIVARS.get(korean_name_key(value))


def species_from_korean_name(value: str | None) -> str | None:
    """A published Korean tree name as a species value, or ``None``.

    * a name from `KOREAN_SPECIES` when the value is one we resolve;
    * the value **unchanged** when the shared hygiene already understands it
      (`form_sentinel_for`, `is_not_a_tree`) -- the same branch
      `shared.species.japanese` has, so a future Korean label the shared
      sentinels learn needs no change here;
    * ``None`` otherwise, which publishes as ``Unknown``.

    `is_korean_non_tree` values are *not* handled here: the caller drops those
    rows, because `enforce_tree_schema` cannot recognise them.
    """
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    resolved = KOREAN_SPECIES.get(korean_name_key(text))
    if resolved is not None:
        return resolved
    if is_not_a_tree(text) or form_sentinel_for(text) is not None:
        return text
    return None
