from __future__ import annotations

from dataclasses import dataclass
import html
import os
import re
import sys
import time

import requests

from shared.ingest import SPECIES_SENTINELS
from shared.wcvp import Checklist, ChecklistUnavailable


HEADERS = {"User-Agent": "sf-tree-enrichment/1.0 (github.com/greenmtnboy/tree_reporting)"}


@dataclass
class SourceTexts:
    wikipedia: str | None
    wcvp: str | None
    gbif: str | None
    selectree: str | None


def normalize_enrichment_search_name(scientific_name: str) -> str:
    tokens = scientific_name.split()
    if len(tokens) < 2:
        return scientific_name
    genus, species_epithet = tokens[0], tokens[1]
    if not re.fullmatch(r"[A-Z][A-Za-z.-]*", genus):
        return scientific_name
    # A hybrid binomial keeps its mark: `Platanus x hispanica 'Bloodgood'`
    # is searched as `Platanus x hispanica`.
    if species_epithet == "x" and len(tokens) >= 3 and re.fullmatch(r"[a-z-]+", tokens[2]):
        return f"{genus} x {tokens[2]}"
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


def _wikipedia_get(url: str, **kwargs) -> requests.Response:
    """A Wikipedia request that waits out a 429 instead of reading it as
    "no article": Wikimedia rate-limits bursts, and a silent miss here is a
    species enriched from Kew's names alone."""
    for attempt in range(3):
        response = requests.get(url, headers=HEADERS, timeout=10, **kwargs)
        if response.status_code != 429:
            return response
        wait = int(response.headers.get("Retry-After") or 0) or 5 * (attempt + 1)
        time.sleep(min(wait, 60))
    return response


# The article sections that carry what POWO's descriptions did -- the plant's
# form, leaves, flowers and fruit, where it grows and how it is planted.  The
# intro alone is a sentence or two for most species.
WIKIPEDIA_SECTIONS = re.compile(
    r"^(description|morphology|characteristics|botany|appearance|identification|"
    r"distribution|range|habitat|ecology|cultivation|uses|horticulture|"
    # The same headings in the fallback languages below.
    r"descripci[oó]n|descri[cç][aã]o|beschreibung|merkmale|distribuci[oó]n|"
    r"distribui[cç][aã]o|r[eé]partition|verbreitung|vorkommen|h[aá]bitat|"
    r"ecolog[ií]a|[eé]cologie|[oö]kologie|cultivo|culture|usos|utilisation|nutzung)",
    re.IGNORECASE,
)
# When English Wikipedia has no article, the taxon's other articles are found
# through Wikidata (its taxon-name property, P225) and read in this order.
# Bogota's Andean species are mostly on Spanish Wikipedia, the hybrid limes on
# German and French.  The Cebuano, Waray, Swedish and Vietnamese wikis carry
# far more taxa, but those are bot-made stubs of a sentence, so are not listed.
WIKIPEDIA_FALLBACK_LANGUAGES = ("en", "es", "pt", "fr", "de", "it", "nl", "ca", "pl", "cs", "ja", "zh")
WIKIPEDIA_SECTION_CHARS = 2500
WIKIPEDIA_SECTIONS_CHARS = 6000
_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def wikipedia_article_parts(extract: str) -> tuple[str, list[tuple[str, str]]]:
    """An article's plain-text extract, split into its intro and the wanted
    top-level sections (each with its subsections), in article order."""
    headings = list(_HEADING.finditer(extract))
    intro = extract[: headings[0].start()].strip() if headings else extract.strip()
    sections: list[tuple[str, str]] = []
    total = 0
    for i, heading in enumerate(headings):
        if len(heading.group(1)) != 2 or not WIKIPEDIA_SECTIONS.match(heading.group(2)):
            continue
        end = next((h.start() for h in headings[i + 1 :] if len(h.group(1)) == 2), len(extract))
        body = _HEADING.sub(lambda m: m.group(2) + ":", extract[heading.end() : end]).strip()
        body = re.sub(r"\n{2,}", "\n", body)[:WIKIPEDIA_SECTION_CHARS]
        if not body or total >= WIKIPEDIA_SECTIONS_CHARS:
            continue
        body = body[: WIKIPEDIA_SECTIONS_CHARS - total]
        total += len(body)
        sections.append((heading.group(2), body))
    return intro, sections


def fetch_wikipedia_text(scientific_name: str) -> str | None:
    for query_name in enrichment_query_candidates(scientific_name):
        slug = query_name.replace(" ", "_")
        summary_extract: str | None = None

        response = _wikipedia_get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}")
        if response.status_code == 200:
            data = response.json()
            extract = data.get("extract", "")
            if extract:
                summary_extract = extract

        response = _wikipedia_get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": True,
                "exsectionformat": "wiki",
                "titles": query_name,
                "format": "json",
                "redirects": 1,
            },
        )
        if response.status_code == 200:
            pages = response.json().get("query", {}).get("pages", {})
            for page in pages.values():
                if page.get("pageid", -1) == -1:
                    continue
                intro, sections = wikipedia_article_parts(page.get("extract", ""))
                if not intro and not sections:
                    continue
                best = summary_extract if summary_extract and len(summary_extract) > len(intro) else intro
                text = "\n\n".join([best, *(f"Wikipedia section {title}:\n{body}" for title, body in sections)])
                if query_name != scientific_name:
                    return f"Wikipedia search query normalized to: {query_name}\n{text}"
                return text

        if summary_extract:
            if query_name != scientific_name:
                return f"Wikipedia search query normalized to: {query_name}\n{summary_extract}"
            return summary_extract

    return fetch_wikidata_linked_article(scientific_name)


def _wikipedia_article(language: str, title: str) -> tuple[str, list[tuple[str, str]]] | None:
    response = _wikipedia_get(
        f"https://{language}.wikipedia.org/w/api.php",
        params={
            "action": "query", "prop": "extracts", "explaintext": True, "exsectionformat": "wiki",
            "titles": title, "format": "json", "redirects": 1,
        },
    )
    if response.status_code != 200:
        return None
    for page in response.json().get("query", {}).get("pages", {}).values():
        if page.get("pageid", -1) != -1 and page.get("extract"):
            return wikipedia_article_parts(page["extract"])
    return None


def fetch_wikidata_linked_article(scientific_name: str) -> str | None:
    """The taxon's article in another title or language, for an English miss.

    Wikidata holds the taxon under its exact name (with `×` for a hybrid) and
    lists every article written about it: an English one under a title the
    name does not redirect from, or one in another language.
    """
    for query_name in enrichment_query_candidates(scientific_name):
        taxon = query_name.replace(" x ", " × ")
        if taxon != query_name:
            article = _wikipedia_article("en", taxon)
            if article and (article[0] or article[1]):
                intro, sections = article
                text = "\n\n".join([intro, *(f"Wikipedia section {title}:\n{body}" for title, body in sections)])
                return f"Wikipedia article {taxon!r}:\n{text}"
        response = _wikipedia_get(
            "https://www.wikidata.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": f'haswbstatement:"P225={taxon}"',
                "srlimit": 1, "format": "json",
            },
        )
        if response.status_code != 200:
            continue
        hits = response.json().get("query", {}).get("search", [])
        if not hits:
            continue
        item = hits[0]["title"]
        response = _wikipedia_get(
            "https://www.wikidata.org/w/api.php",
            params={"action": "wbgetentities", "ids": item, "props": "sitelinks", "format": "json"},
        )
        if response.status_code != 200:
            continue
        sitelinks = response.json().get("entities", {}).get(item, {}).get("sitelinks", {})
        for language in WIKIPEDIA_FALLBACK_LANGUAGES:
            link = sitelinks.get(f"{language}wiki")
            if not link:
                continue
            article = _wikipedia_article(language, link["title"])
            if not article or not (article[0] or article[1]):
                continue
            intro, sections = article
            text = "\n\n".join([intro, *(f"Wikipedia section {title}:\n{body}" for title, body in sections)])
            return f"Wikipedia ({language}) article {link['title']!r}, via Wikidata {item}:\n{text}"
    return None


# Kew's checklist (WCVP), the names and distributions POWO served.  POWO's API
# refuses scripted requests since late 2026; `refresh-wcvp` publishes the
# checklist as parquets, read here through `shared.wcvp.Checklist`.  It
# carries status, accepted name, family, lifeform, climate, a one-line range
# and every TDWG level-3 area native or introduced -- what the native-range
# evidence and the ecoregion shortlist were built from -- but none of POWO's
# prose descriptions; those come from Wikipedia's article sections.
#
# One reader per process.  `prefetch_wcvp` loads every name a run will ask
# about before the loop, so the parquets are scanned a few times per run,
# not once per species.  `WCVP_ZIP` reads a local copy of Kew's zip instead.
_WCVP: Checklist | None = None
_WCVP_DISABLED = False
WCVP_AREA_NAMES = 60
WCVP_AREA_DETAILS = 12


def wcvp_checklist() -> Checklist | None:
    global _WCVP
    if _WCVP_DISABLED:
        return None
    if _WCVP is None:
        _WCVP = Checklist(os.environ.get("WCVP_ZIP"))
    return _WCVP


def _disable_wcvp(error: Exception) -> None:
    """Carry on without Kew, loudly: the other three sources still answer,
    but every row enriched now has no structured range behind its nativeness."""
    global _WCVP_DISABLED
    _WCVP_DISABLED = True
    print(f"[warn] WCVP unavailable, enriching without it: {error}", file=sys.stderr)


def _taxon_ids(kew: Checklist, query_name: str) -> list[int]:
    return [
        r["accepted_plant_name_id"] if r["accepted_plant_name_id"] is not None else r["plant_name_id"]
        for r in kew.records(query_name)
    ]


def prefetch_wcvp(scientific_names) -> None:
    kew = wcvp_checklist()
    if kew is None:
        return
    queries = [q for name in scientific_names for q in enrichment_query_candidates(name)]
    try:
        kew.prefetch(queries)
        kew.prefetch_taxa(i for q in queries for i in _taxon_ids(kew, q))
    except ChecklistUnavailable as e:
        _disable_wcvp(e)


def _wcvp_name(record: dict) -> str:
    return " ".join(p for p in (record["taxon_name"], record["taxon_authors"]) if p)


def _wcvp_area_lines(areas: list[dict]) -> list[str]:
    buckets = {
        "natives": [a for a in areas if not a["introduced"] and not a["extinct"] and not a["location_doubtful"]],
        "introduced": [a for a in areas if a["introduced"] and not a["extinct"]],
        "extinct": [a for a in areas if a["extinct"]],
        "uncertain": [a for a in areas if a["location_doubtful"] and not a["introduced"] and not a["extinct"]],
    }
    parts: list[str] = []
    for bucket, entries in buckets.items():
        if not entries:
            continue
        entries = sorted(entries, key=lambda a: (a["continent"] or "", a["region"] or "", a["area"] or ""))
        names = [a["area"] for a in entries if a["area"]]
        more = f" (+{len(names) - WCVP_AREA_NAMES} more)" if len(names) > WCVP_AREA_NAMES else ""
        parts.append(f"Distribution {bucket}: {', '.join(names[:WCVP_AREA_NAMES])}{more}")
        establishment = "Introduced" if bucket == "introduced" else "Native"
        details = [
            f"name={a['area']}; tdwg={a['area_code_l3']}; "
            f"path={(a['continent'] or '').title()} > {a['region']} > {a['area']}; "
            f"establishment={establishment}"
            for a in entries[:WCVP_AREA_DETAILS]
        ]
        parts.append(f"Distribution {bucket} detail: {' | '.join(details)}")
    return parts


def _wcvp_taxon_lines(kew: Checklist, taxon: dict) -> list[str]:
    parts = [f"WCVP family: {taxon['family']}"] if taxon["family"] else []
    if taxon["taxon_rank"]:
        parts.append(f"WCVP rank: {taxon['taxon_rank']}")
    if taxon["lifeform_description"]:
        parts.append(f"Lifeform: {taxon['lifeform_description']}")
    if taxon["climate_description"]:
        parts.append(f"Climate: {taxon['climate_description']}")
    if taxon["geographic_area"]:
        parts.append(f"WCVP native range: {taxon['geographic_area']}")
    return parts + _wcvp_area_lines(kew.areas.get(taxon["plant_name_id"], []))


def _wcvp_text(kew: Checklist, scientific_name: str, query_name: str) -> str | None:
    records = kew.records(query_name)
    if not records:
        return None
    kew.prefetch_taxa(_taxon_ids(kew, query_name))
    parts: list[str] = []
    if query_name != scientific_name:
        parts.append(f"WCVP query normalized to: {query_name}")
    accepted = [r for r in records if r["accepted_plant_name_id"] == r["plant_name_id"]]
    synonyms = [r for r in records if r not in accepted and r["accepted_plant_name_id"] is not None]
    taxon: dict | None = None
    if accepted:
        taxon = accepted[0]
        parts.append(f"WCVP matched name: {_wcvp_name(taxon)}")
        parts.append("Taxonomic status: Accepted")
        # A homonym is reported, never followed: `Quercus lyrata` Walter is
        # the overcup oak whatever Spreng. meant by the same name.
        for r in synonyms:
            parts.append(
                f"WCVP also lists {_wcvp_name(r)} (a different author) as a synonym of {r['accepted_taxon_name']}"
            )
    else:
        targets = {r["accepted_plant_name_id"]: r for r in synonyms}
        if len(targets) == 1:
            record = next(iter(targets.values()))
            parts.append(f"WCVP matched name: {_wcvp_name(record)}")
            parts.append(f"Taxonomic status: {record['taxon_status']}")
            parts.append(f"WCVP accepted name via synonym: {record['accepted_taxon_name']}")
            taxon = kew.by_id.get(record["accepted_plant_name_id"])
        elif targets:
            parts.append(
                f"WCVP lists {query_name} under {len(targets)} different accepted taxa: "
                + ", ".join(sorted(r["accepted_taxon_name"] for r in targets.values()))
                + "; the name is ambiguous"
            )
        else:
            parts.append(f"WCVP matched name: {_wcvp_name(records[0])}")
            parts.append(f"Taxonomic status: {records[0]['taxon_status']} (not placed in an accepted taxon)")
    # The trade's usage, which is often what the trees are: nurseries sold
    # C. laevigata as `Crataegus oxyacantha` long after the name was sunk.
    misapplied = sorted({r["accepted_taxon_name"] for r in kew.misapplied(query_name) if r["accepted_taxon_name"]})
    if misapplied:
        parts.append(f"WCVP notes the name has also been misapplied (auct.) to: {', '.join(misapplied)}")
    if taxon:
        parts.extend(_wcvp_taxon_lines(kew, taxon))
    return "\n".join(parts)


def fetch_wcvp_text(scientific_name: str) -> str | None:
    kew = wcvp_checklist()
    if kew is None:
        return None
    try:
        for query_name in enrichment_query_candidates(scientific_name):
            text = _wcvp_text(kew, scientific_name, query_name)
            if text:
                return text
    except ChecklistUnavailable as e:
        _disable_wcvp(e)
    return None


# `family` comes from Kew and from nowhere else: it is a fact about the name,
# WCVP states it for every record, and a model asked for it would be guessing
# at something that can be looked up.


def _single_family(records: list[dict]) -> str | None:
    """The one family these records agree on, or None.

    Accepted records are read first and the rest only when there are none:
    Kew lists `Ilex` Mill. (a synonym in Fagaceae) beside `Ilex` L., and
    `Syringa`, `Lonicera`, `Phoenix` and `Cedrus` each have a sunk homonym in
    another family, so asking every record to agree would refuse most of the
    genera a street is planted with.  Two accepted records in two families,
    or no accepted record and a disagreement, is ambiguous and answers None.
    """
    accepted = {
        r["family"] for r in records
        if r["family"] and r["accepted_plant_name_id"] == r["plant_name_id"]
    }
    if accepted:
        return next(iter(accepted)) if len(accepted) == 1 else None
    every = {r["family"] for r in records if r["family"]}
    return next(iter(every)) if len(every) == 1 else None


def family_genus_candidates(name: str) -> list[str]:
    """The genus of *name* as Kew may key it: bare, and as a nothogenus."""
    tokens = name.replace(chr(215), " x ").split()
    if len(tokens) > 1 and tokens[0].lower() == "x":
        tokens = tokens[1:]
    if not tokens or not re.fullmatch(r"[A-Z][A-Za-z-]+", tokens[0]):
        return []
    return [tokens[0], f"x {tokens[0]}"]


def family_from_checklist(kew: Checklist, name: str | None) -> str | None:
    """The family Kew places *name* in.

    The name's own records first; failing that its genus, when the genus is
    unambiguous (`_single_family` over the genus-rank records).  The fallback
    is what names a cultivar, a `sp.`, a trade name or a misspelt epithet --
    the family of an `Acer` is not in doubt because its epithet is.  A
    sentinel is never looked up: `Cactus` is a growth form here and a genus
    (a synonym of Mammillaria) at Kew.
    """
    if not name or name in SPECIES_SENTINELS:
        return None
    family = _single_family(kew.records(name))
    if family:
        return family
    for genus in family_genus_candidates(name):
        family = _single_family([r for r in kew.records(genus) if r["taxon_rank"] == "Genus"])
        if family:
            return family
    return None


def wcvp_family(scientific_name: str) -> str | None:
    """`family_from_checklist` against the run's reader; None without Kew."""
    kew = wcvp_checklist()
    if kew is None:
        return None
    try:
        return family_from_checklist(kew, scientific_name)
    except ChecklistUnavailable as e:
        _disable_wcvp(e)
        return None


def with_wcvp_family(table):
    """Fill every null `family` Kew can name; a value already there is kept.

    Run on load, like the other `with_*` steps, so the column heals itself:
    a row written before the job filled `family`, or on a day the checklist
    could not be read, is named on the next load, and one lookup covers the
    whole table (the names still missing and their genera, prefetched
    together).  Once the table is filled this asks only about the few names
    Kew does not have.  Without Kew the table is returned as it came.
    """
    import pyarrow as pa

    if "family" not in table.schema.names or len(table) == 0:
        return table
    species = table.column("species").to_pylist()
    family = table.column("family").to_pylist()
    missing = [s for s, f in zip(species, family) if s and not f and s not in SPECIES_SENTINELS]
    if not missing:
        return table
    kew = wcvp_checklist()
    if kew is None:
        return table
    try:
        kew.prefetch([*missing, *(g for s in missing for g in family_genus_candidates(s))])
        filled = [f or family_from_checklist(kew, s) for s, f in zip(species, family)]
    except ChecklistUnavailable as e:
        _disable_wcvp(e)
        return table
    named = sum(1 for before, after in zip(family, filled) if after and not before)
    if not named:
        return table
    print(
        f"[family] filled family on {named} row(s) from WCVP; "
        f"{len(missing) - named} name(s) Kew cannot place",
        file=sys.stderr,
    )
    idx = table.schema.get_field_index("family")
    return table.set_column(idx, "family", pa.array(filled, type=pa.string()))


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


def selectree_memo_names_taxon(memo: str, scientific_name: str) -> bool:
    """Whether a SelecTree memo is about the tree it is filed under.

    Some memos are generic prose that never names the plant, and some of those
    describe a different one: Acer campestre's says "striking white bark that
    peels away in strips" beside its own bark fields, Furrowed and Dark Brown
    or Dark Gray; Abies amabilis's gives a fir oval leaves. The model repeats
    them. The memos that are right name the taxon, so a memo that does not
    name the genus is dropped; the structured fields stay.
    """
    genus = scientific_name.split()[0].lower() if scientific_name.strip() else ""
    return bool(genus) and genus in memo.lower()


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
            memo = _clean_selectree_value(detail.get("memo"))
            if memo and selectree_memo_names_taxon(memo, scientific_name):
                parts.append(f"SelecTree memo: {memo}")
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
        wcvp=fetch_wcvp_text(scientific_name),
        gbif=fetch_gbif_text(scientific_name),
        selectree=fetch_selectree_text(scientific_name),
    )


def source_labels(texts: SourceTexts) -> list[str]:
    labels = []
    if texts.wikipedia:
        labels.append("Wikipedia")
    if texts.wcvp:
        labels.append("WCVP")
    if texts.gbif:
        labels.append("GBIF")
    if texts.selectree:
        labels.append("SelecTree")
    return labels


def build_reference_text(texts: SourceTexts) -> str:
    context_parts = []
    if texts.wikipedia:
        context_parts.append(f"Wikipedia:\n{texts.wikipedia}")
    if texts.wcvp:
        context_parts.append(f"World Checklist of Vascular Plants (WCVP / Kew):\n{texts.wcvp}")
    if texts.gbif:
        context_parts.append(f"GBIF Species Match:\n{texts.gbif}")
    if texts.selectree:
        context_parts.append(f"SelecTree:\n{texts.selectree}")
    return "\n\n".join(context_parts)
