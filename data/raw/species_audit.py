#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "pyarrow", "pytrilogy", "requests"]
# ///
"""Find species names two cities spell differently, and ask POWO which is right.

    cd data/raw && uv run species_audit.py                 # report, all cities
    cd data/raw && uv run species_audit.py --city USDEN    # one city's names
    cd data/raw && uv run species_audit.py --map           # emit map entries

Inventories disagree about spelling, and `species` is the join key into the
enrichment table, so a disagreement is not cosmetic: `Liquidambar stryaciflua`
and `Liquidambar styraciflua` are two enrichment rows, two LLM calls, two
entries in every species rollup and two colours on the map, for one tree.  The
September 2026 sweep mapped 232 such names over 167,813 published trees --
67 hybrid-mark pairs and 165 misspellings -- which reclaimed 178 enrichment
rows the LLM had been paid for twice and took the fleet from 4,172 distinct
species to 3,912.  It refused 57 more pairs as two real taxa.

Re-run it after a city lands, and expect it to report nothing: a name the maps
already fold is no longer a candidate, so a clean sweep is the converged
state rather than a sign the tool did not run.

`SPECIES_SYNONYMS` and `SPECIES_MISSPELLINGS` in `_ingest_shared.py` are what
fixes one, and this is the tool that decides what belongs in them.

**The measurement is cheap and the judgement is not, which is the whole point
of asking Kew rather than a heuristic.**  Finding candidates is a Levenshtein
join over the published `species` columns and takes seconds.  Deciding whether
a pair is one taxon spelled two ways or two real taxa two edits apart is
botany, and getting it backwards is the expensive direction: folding
`Acer saccharum` into `Acer saccharinum` would relabel 33,644 sugar maples as
silver maple, and nothing downstream would report it.  The near-miss pairs in
the published data include `Celtis`/`Cercis occidentalis`,
`Malus`/`Taxus baccata`, `Prunus`/`Pinus nigra`, `Cornus`/`Morus alba`,
`Ulmus`/`Alnus rubra`, `Quercus lobata`/`lyrata` and `Laburnum`/`Viburnum` --
every one a genuine pair of species, and every one indistinguishable by shape
from a typo.

So each name is resolved against POWO's search API (the same service
`enrichment/_tree_enrichment_sources.py` reads for enrichment context) and the
pair is classified by what comes back, never by tree counts:

    both names known to Kew   two real taxa          -> refuse, never map
    one a synonym of other    nomenclature           -> SPECIES_SYNONYMS
    one unknown to POWO,      a name that does not   -> SPECIES_MISSPELLINGS
      the other accepted        exist                     (or SPECIES_SYNONYMS
                                                           when only the hybrid
                                                           mark differs)
    anything else             unresolved             -> reported, not mapped

"Known to Kew" is broader than "accepted", deliberately.  A binomial can carry
more than one record -- `Crataegus crus-galli` is both an accepted name and,
under another author, a synonym of `C. calpodendron` -- and that ambiguity
settles nothing about nomenclature while settling everything about spelling.
Such a name refuses a *synonym* verdict and still serves as the target of a
misspelling, which is what keeps `Crataegus crusgalli` (1,529 trees) mappable.

An exact match is required: POWO's search is fuzzy and answers
`Acer platenoides` with *Acer platanoides*, so "did it return a result" is not
the question -- "did it return **this name**" is.  A name it cannot match at
all is not a taxon, which is the evidence a misspelling entry rests on.

Tree counts are deliberately not evidence.  They look like they should be --
the wrong spelling is usually the rarer one -- but `Larix siberica` has 8,431
trees against 7,661 for the correct `Larix sibirica`, and `Tilia europaea`
outnumbers `Tilia x europaea` 55,002 to 3,137.  Whichever city is bigger wins
a vote like that, which is not the same as being right.  Counts are printed
because they size the fix, and are used only to break a tie POWO could not.

Responses are cached in `species_audit_cache.json` (gitignored) so a re-run
costs nothing and the sweep is resumable; delete it to re-ask Kew.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _ingest_shared import (  # noqa: E402
    MUNICIPAL_DATA_SOURCES,
    SPECIES_MISSPELLINGS,
    SPECIES_SYNONYMS,
    sanitize_species,
)

DATA_VERSION = "2"
TREES_URL = (
    "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/"
    "{code}_tree_info_v" + DATA_VERSION + ".parquet"
)
POWO_SEARCH = "https://powo.science.kew.org/api/2/search"
HEADERS = {"User-Agent": "arborary-species-audit/1.0 (+https://arborary.world)"}
CACHE_PATH = Path(__file__).parent / "species_audit_cache.json"

# Two edits apart is where real pairs start crowding in (`Acer saccharum` and
# `Acer saccharinum` are two), so the join casts wide and POWO does the
# filtering.  Below eight characters an edit is too large a fraction of the
# name for the comparison to mean anything.
MAX_EDITS = 2
MIN_LENGTH = 8


# ---------------------------------------------------------------------------
# POWO
# ---------------------------------------------------------------------------


def _norm(name: str | None) -> str:
    """Compare names the way the ingest writes them: ASCII mark, folded case."""
    if not name:
        return ""
    ascii_mark = unicodedata.normalize("NFKD", name).replace("×", "x")
    return " ".join(ascii_mark.lower().split())


class Powo:
    """POWO search, exact-match only, cached on disk."""

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.cache: dict[str, dict] = (
            json.loads(path.read_text("utf8")) if path.exists() else {}
        )

    def save(self) -> None:
        self.path.write_text(json.dumps(self.cache, indent=0, sort_keys=True), "utf8")

    def lookup(self, name: str) -> dict:
        """`{'status': accepted|synonym|unplaced|not_found|ambiguous|error}`.

        `not_found` means POWO returned nothing whose name *is* this name --
        the fuzzy hit on the correctly spelled neighbour is discarded on
        purpose, since it is the reason a misspelling looks resolvable.

        **Every** exact match is read, not the first, because a binomial can be
        published more than once by different authors and the records need not
        agree.  `Quercus lyrata` is the one that proves it: Walter's is the
        accepted overcup oak and Spreng.'s is a synonym of `Quercus lobata`,
        POWO returns both, and taking whichever came back first said the
        overcup oak was really the valley oak -- which would have relabelled
        2,773 trees.  Disagreement is reported as `ambiguous`, never resolved
        by picking one.

        A transport failure is retried, then recorded as `error` rather than
        as `not_found`: an unreachable Kew must never read as evidence that a
        name does not exist.
        """
        if name in self.cache:
            return self.cache[name]
        import requests

        result = {"status": "error", "accepted": None, "matched": None, "family": None}
        for attempt in range(3):
            try:
                response = requests.get(
                    POWO_SEARCH,
                    params={"q": name, "f": "species_f"},
                    headers=HEADERS,
                    timeout=20,
                )
                if response.status_code != 200:
                    time.sleep(2 * (attempt + 1))
                    continue
                hits = response.json().get("results") or []
                exact = [h for h in hits if _norm(h.get("name")) == _norm(name)]
                if not exact:
                    result = {
                        "status": "not_found",
                        "accepted": None,
                        "matched": None,
                        "family": None,
                    }
                else:
                    readings = set()
                    for hit in exact:
                        synonym_of = (
                            hit.get("synonymOf") if not hit.get("accepted", True) else None
                        )
                        readings.add(
                            (
                                "synonym"
                                if synonym_of
                                else ("accepted" if hit.get("accepted") else "unplaced"),
                                (synonym_of or {}).get("name"),
                            )
                        )
                    if len(readings) > 1:
                        result = {
                            "status": "ambiguous",
                            "accepted": None,
                            "matched": exact[0].get("name"),
                            "family": exact[0].get("family"),
                            "readings": sorted(
                                f"{s}:{a or '-'}" for s, a in readings
                            ),
                        }
                    else:
                        status, accepted = readings.pop()
                        result = {
                            "status": status,
                            "accepted": accepted,
                            "matched": exact[0].get("name"),
                            "family": exact[0].get("family"),
                        }
                break
            except Exception:
                time.sleep(2 * (attempt + 1))
        self.cache[name] = result
        return result


# ---------------------------------------------------------------------------
# candidates
# ---------------------------------------------------------------------------


def published_species(cities: list[str]) -> list[tuple[str, int, int]]:
    """`(species, trees, cities)` over every city parquet that exists.

    A city with no parquet yet is skipped rather than failed, the same carve-out
    `parquetSchema.test.ts` makes: a city added this week has nothing published
    to audit and must not take the sweep down.
    """
    import duckdb
    import urllib.request

    live = []
    for code in cities:
        url = TREES_URL.format(code=code.lower())
        try:
            request = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status == 200:
                    live.append(url)
        except Exception:
            print(f"  {code}: no published parquet yet, skipping", file=sys.stderr)
    if not live:
        raise SystemExit("no published city parquets found")
    con = duckdb.connect()
    con.execute("install httpfs; load httpfs;")
    files = ",".join(f"'{u}'" for u in live)
    return con.execute(
        f"""
        select species, sum(n) trees, count(*) cities from (
            select species, city, count(*) n
            from read_parquet([{files}], union_by_name=true)
            where species is not null group by 1, 2
        ) group by 1
        """
    ).fetchall()


def candidate_pairs(rows: list[tuple[str, int, int]]) -> list[tuple]:
    """Every pair of published names within MAX_EDITS of each other.

    Only names the ingest still emits as written are considered.  A published
    parquet is a snapshot of whatever `sanitize_species` did when its city was
    last built, so it holds values the rule already rewrites -- and those need
    no map entry, because the next rebuild fixes them.  Auditing them anyway
    invites the worst kind of entry: `Viburnum genus` is a placeholder the
    epithet rule now truncates, and it is two edits from `Viburnum tinus`.
    """
    import duckdb

    rows = [r for r in rows if sanitize_species(r[0]) == r[0]]
    con = duckdb.connect()
    con.execute("create table s (species varchar, trees bigint, cities bigint)")
    con.executemany("insert into s values (?, ?, ?)", rows)
    return con.execute(
        f"""
        select a.species, a.trees, a.cities, b.species, b.trees, b.cities
        from s a join s b
          on a.species < b.species
         and levenshtein(a.species, b.species) <= {MAX_EDITS}
        where length(a.species) >= {MIN_LENGTH}
          and length(b.species) >= {MIN_LENGTH}
        """
    ).fetchall()


# ---------------------------------------------------------------------------
# adjudication
# ---------------------------------------------------------------------------


def _bare(name: str) -> str:
    """The name with its hybrid mark removed, to spot a mark-only difference."""
    return name.replace(" x ", " ").replace(" × ", " ")


def _edits(a: str, b: str) -> int:
    """Levenshtein, for comparing one token against another."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _too_close_to_call(wrong: str, right_name: str) -> bool:
    """True when the pair differs by two edits inside a very short token.

    Distance is a fraction of the word, not an absolute: two edits in
    `soulangiana` is a slip, and two edits in `mazei` is a different word.
    That matters most for hybrids, whose epithets are people's surnames --
    `Quercus x mazei` and `Quercus x warei` are two edits apart and two
    different named hybrids, and POWO cannot rule on it because it has no
    record of the first.  So the tool declines instead of guessing.
    """
    left, right = wrong.split(), right_name.split()
    if len(left) != len(right):
        return False
    differing = [(x, y) for x, y in zip(left, right) if x != y]
    if len(differing) != 1:
        return False
    x, y = differing[0]
    return _edits(x, y) >= 2 and min(len(x), len(y)) <= 5


def _is_real_name(reading: dict) -> bool:
    """POWO has *some* exact record for it, so it is not a typo.

    `ambiguous` counts here.  It means two records share the name -- which
    settles nothing about nomenclature, and everything about spelling: a name
    Kew has published twice is a name.
    """
    return reading["status"] in ("accepted", "synonym", "unplaced", "ambiguous")


def _is_accepted_target(reading: dict) -> bool:
    """Safe to fold a misspelling onto: at least one reading is accepted.

    A name whose every record is a synonym is not -- `Crataegus oxyacantha`
    resolves three different ways, none of them to itself, and mapping onto it
    would build a chain `sanitize_species` cannot follow in one lookup.
    """
    if reading["status"] == "accepted":
        return True
    return reading["status"] == "ambiguous" and any(
        r.startswith("accepted:") for r in reading.get("readings") or []
    )


def _ambiguous_keys(pairs: list[tuple], powo: Powo) -> set[str]:
    """Names that are within MAX_EDITS of more than one real name.

    A misspelling is only resolvable when there is one thing it could have
    meant.  `Picea pugens` is one edit from `Picea pungens` *and* from
    `Picea rubens`, both accepted, and nothing in the name says which the
    surveyor typed -- so it stays unmapped rather than being guessed.
    """
    targets: dict[str, set[str]] = {}
    for a, _at, _ac, b, _bt, _bc in pairs:
        left, right = powo.lookup(a), powo.lookup(b)
        for name, other, other_reading in ((a, b, right), (b, a, left)):
            if powo.lookup(name)["status"] == "not_found" and _is_real_name(
                other_reading
            ):
                targets.setdefault(name, set()).add(other)
    return {name for name, seen in targets.items() if len(seen) > 1}


def classify(pairs: list[tuple], powo: Powo) -> dict[str, list]:
    """Sort each pair into a verdict.  POWO decides; counts only break ties."""
    verdicts: dict[str, list] = {
        "MISSPELLING": [],
        "HYBRID_MARK": [],
        "SYNONYM": [],
        "REFUSED": [],
        "UNRESOLVED": [],
    }
    ambiguous = _ambiguous_keys(pairs, powo)
    for a, a_trees, a_cities, b, b_trees, b_cities in pairs:
        left, right = powo.lookup(a), powo.lookup(b)
        trees = {a: a_trees, b: b_trees}

        if "error" in (left["status"], right["status"]):
            verdicts["UNRESOLVED"].append((a, b, "POWO unreachable", 0))
            continue

        # A synonym claim needs one unambiguous record on each side: this is
        # the verdict that asserts nomenclature rather than spelling.
        if "ambiguous" not in (left["status"], right["status"]):
            if _norm(left.get("accepted")) == _norm(b):
                verdicts["SYNONYM"].append((a, b, "POWO: synonym", trees[a]))
                continue
            if _norm(right.get("accepted")) == _norm(a):
                verdicts["SYNONYM"].append((b, a, "POWO: synonym", trees[b]))
                continue

        # Two names Kew has both heard of are two names, whatever their shapes.
        if _is_real_name(left) and _is_real_name(right):
            why = (
                "both accepted taxa"
                if left["status"] == right["status"] == "accepted"
                else "both are real published names"
            )
            verdicts["REFUSED"].append((a, b, why, min(trees.values())))
            continue

        missing = [n for n, r in ((a, left), (b, right)) if r["status"] == "not_found"]
        known = [n for n, r in ((a, left), (b, right)) if _is_real_name(r)]
        if len(missing) != 1 or len(known) != 1:
            verdicts["UNRESOLVED"].append(
                (a, b, f"{left['status']} / {right['status']}", min(trees.values()))
            )
            continue
        wrong, right_name = missing[0], known[0]
        if wrong in ambiguous:
            verdicts["REFUSED"].append(
                (wrong, right_name, "could have meant either of two names", trees[wrong])
            )
            continue
        if _too_close_to_call(wrong, right_name):
            verdicts["REFUSED"].append(
                (wrong, right_name, "two edits in a short word: a different name", trees[wrong])
            )
            continue
        if not _is_accepted_target(powo.lookup(right_name)):
            verdicts["UNRESOLVED"].append(
                (wrong, right_name, "target is itself a synonym", trees[wrong])
            )
            continue
        bucket = "HYBRID_MARK" if _bare(wrong) == _bare(right_name) else "MISSPELLING"
        verdicts[bucket].append((wrong, right_name, "POWO: no such name", trees[wrong]))
    return verdicts


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def report(verdicts: dict[str, list]) -> None:
    order = ["MISSPELLING", "HYBRID_MARK", "SYNONYM", "REFUSED", "UNRESOLVED"]
    blurb = {
        "MISSPELLING": "-> SPECIES_MISSPELLINGS  (POWO cannot match the key)",
        "HYBRID_MARK": "-> SPECIES_SYNONYMS      (same taxon, mark omitted or added)",
        "SYNONYM": "-> SPECIES_SYNONYMS      (POWO lists the key under the value)",
        "REFUSED": "   never map              (two real taxa, a near miss by shape)",
        "UNRESOLVED": "   read these by hand",
    }
    for name in order:
        rows = sorted(verdicts[name], key=lambda r: -r[3])
        trees = sum(r[3] for r in rows)
        print(f"\n===== {name}: {len(rows)} pairs, {trees:,} trees {blurb[name]}")
        for wrong, right_name, why, count in rows:
            print(f"  {wrong:<34} -> {right_name:<34} {count:>8,}  {why}")


def emit_map(verdicts: dict[str, list]) -> None:
    """Print the entries for pasting into `_ingest_shared.py`."""
    for name, target in (
        ("HYBRID_MARK", "SPECIES_SYNONYMS"),
        ("SYNONYM", "SPECIES_SYNONYMS"),
        ("MISSPELLING", "SPECIES_MISSPELLINGS"),
    ):
        rows = sorted(verdicts[name])
        print(f"\n# --- {target}: {name} ({len(rows)}) ---")
        for wrong, right_name, _why, _count in rows:
            already = wrong in SPECIES_SYNONYMS or wrong in SPECIES_MISSPELLINGS
            print(f'    "{wrong}": "{right_name}",' + ("  # already mapped" if already else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", action="append", help="restrict to these city codes")
    parser.add_argument("--map", action="store_true", help="emit map entries to paste")
    args = parser.parse_args()

    cities = args.city or sorted(MUNICIPAL_DATA_SOURCES)
    print(f"reading {len(cities)} cities...", file=sys.stderr)
    rows = published_species([c.upper() for c in cities])
    pairs = candidate_pairs(rows)
    print(
        f"{len(rows):,} distinct species, {len(pairs)} pairs within {MAX_EDITS} edits;"
        f" asking POWO...",
        file=sys.stderr,
    )

    powo = Powo()
    names = sorted({p[0] for p in pairs} | {p[3] for p in pairs})
    todo = [n for n in names if n not in powo.cache]
    for i, name in enumerate(todo, 1):
        powo.lookup(name)
        time.sleep(0.35)
        if i % 25 == 0:
            powo.save()
            print(f"  {i}/{len(todo)}", file=sys.stderr)
    powo.save()

    verdicts = classify(pairs, powo)
    emit_map(verdicts) if args.map else report(verdicts)


if __name__ == "__main__":
    main()
