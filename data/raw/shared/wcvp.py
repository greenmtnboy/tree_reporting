"""Kew's World Checklist of Vascular Plants, the names behind POWO.

POWO (Plants of the World Online) is WCVP's names and distributions with
descriptions on top, and since late 2026 its API sits behind bot protection
that answers this project's requests with 403.  Kew publishes the checklist
itself as a bulk download, which is the supported way to read it in volume:
one pipe-delimited zip holding `wcvp_names.csv` (every name, its status and
the accepted name it points at) and `wcvp_distribution.csv` (each accepted
taxon's TDWG level-3 areas, native or introduced).

`refresh-wcvp` publishes the two as parquets (`wcvp_info.preql`), so a
consumer reads one versioned object instead of each downloading 84 MB, and
nothing from the checklist is committed to the repository.

Licence: CC BY (Kew lists CC BY 3.0 for the v16 release, GBIF's copy CC BY
4.0).  Cite as Govaerts R (ed.). WCVP: World Checklist of Vascular Plants.
Facilitated by the Royal Botanic Gardens, Kew.  `SOURCE_CITATION` travels on
every published row.
"""

from __future__ import annotations

import os
import tempfile
import unicodedata
import zipfile
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import pyarrow as pa

WCVP_ZIP_URL = "https://sftp.kew.org/pub/data-repositories/WCVP/wcvp.zip"
NAMES_MEMBER = "wcvp_names.csv"
DISTRIBUTION_MEMBER = "wcvp_distribution.csv"
SOURCE_CITATION = (
    "Govaerts R (ed.). WCVP: World Checklist of Vascular Plants. "
    "Facilitated by the Royal Botanic Gardens, Kew. CC BY. " + WCVP_ZIP_URL
)

# The columns published from wcvp_names.csv.  The bibliographic ones
# (place_of_publication, volume_and_page, the three author splits) are what
# make the raw table 416 MiB in Arrow and nothing here reads them; the
# `powo_id` / `ipni_id` keep every row joinable back to Kew if one is needed.
NAME_COLUMNS = (
    "plant_name_id", "ipni_id", "powo_id", "taxon_rank", "taxon_status", "family",
    "genus_hybrid", "genus", "species_hybrid", "species", "infraspecific_rank",
    "infraspecies", "taxon_name", "taxon_authors", "accepted_plant_name_id",
    "parent_plant_name_id", "hybrid_formula", "first_published", "geographic_area",
    "lifeform_description", "climate_description", "reviewed",
)
DISTRIBUTION_COLUMNS = (
    "plant_locality_id", "plant_name_id", "continent_code_l1", "continent",
    "region_code_l2", "region", "area_code_l3", "area", "introduced", "extinct",
    "location_doubtful",
)
DISTRIBUTION_FLAGS = ("introduced", "extinct", "location_doubtful")


def name_key(name: str | None) -> str | None:
    """A name as the species columns spell it, folded for an exact lookup.

    WCVP writes the hybrid sign `×` and the ingests write `x`
    (`Platanus × hispanica` / `Platanus x hispanica`); case and runs of
    whitespace differ between portals.  Folding both sides the same way is
    what lets a lookup be exact, which matters: the fuzzy hit on a correctly
    spelled neighbour is precisely what a misspelling must not match.
    Diacritics go too (`microgène` -> `microgene`): the species columns are
    written without them.
    """
    if not name:
        return None
    text = unicodedata.normalize("NFKD", name.replace("×", " x "))
    text = "".join(c for c in text if not unicodedata.combining(c))
    folded = " ".join(text.lower().split())
    return folded or None


def zip_last_modified(response_headers) -> datetime:
    stamp = response_headers.get("Last-Modified")
    if not stamp:
        raise RuntimeError(f"no Last-Modified header on {WCVP_ZIP_URL}")
    return parsedate_to_datetime(stamp)


def fetch_zip() -> Path:
    """The release zip, downloaded once per release per machine.

    Both ingests read the same 84 MB file and usually run in one container,
    so it is cached in the temp directory under the server's ETag: the
    second ingest, and a re-run against an unchanged release, read the copy.
    `WCVP_ZIP` points at a local file instead (tests, a workstation that
    already has one).
    """
    local = os.environ.get("WCVP_ZIP")
    if local:
        return Path(local)
    from shared.ingest import DEFAULT_HEADERS, head_with_retry

    import requests

    head = head_with_retry(WCVP_ZIP_URL, timeout=60)
    tag = "".join(c for c in head.headers.get("ETag", "") if c.isalnum()) or "latest"
    path = Path(tempfile.gettempdir()) / f"wcvp-{tag}.zip"
    if path.exists() and path.stat().st_size == int(head.headers.get("Content-Length", -1)):
        return path
    partial = path.with_suffix(".part")
    with requests.get(WCVP_ZIP_URL, headers=DEFAULT_HEADERS, stream=True, timeout=300) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    partial.replace(path)
    return path


# Typed up front: a streaming read infers each column from its first block,
# and a column null for the first 16 MB would be typed null and then fail.
INT_COLUMNS = frozenset({
    "plant_name_id", "accepted_plant_name_id", "parent_plant_name_id",
    "plant_locality_id", "continent_code_l1", "region_code_l2",
    "introduced", "extinct", "location_doubtful",
})


def column_types(columns: tuple[str, ...]) -> dict[str, pa.DataType]:
    return {c: pa.int64() if c in INT_COLUMNS else pa.string() for c in columns}


def constant_column(value: str, rows: int) -> pa.DictionaryArray:
    """One string on every row, stored once: the citation is 170 bytes, and
    as a plain string column over 1.4M names it was a quarter of a gigabyte."""
    return pa.DictionaryArray.from_arrays(pa.array([0] * rows, type=pa.int8()), pa.array([value]))


def name_key_array(names: pa.ChunkedArray) -> pa.ChunkedArray:
    """`name_key` over a column, in Arrow: 1.4M Python strings cost ~300 MB."""
    import pyarrow.compute as pc

    text = pc.replace_substring(names, "×", " x ")
    text = pc.utf8_normalize(text, form="NFKD")
    text = pc.replace_substring_regex(text, r"\p{Mn}", "")
    text = pc.utf8_lower(text)
    text = pc.replace_substring_regex(text, r"\s+", " ")
    text = pc.utf8_trim_whitespace(text)
    return pc.if_else(pc.equal(text, ""), pa.scalar(None, pa.string()), text)


def read_member(path: Path, member: str, columns: tuple[str, ...]) -> pa.Table:
    """One CSV from the zip, as Arrow.

    Pipe-delimited with no quoting: a quote character is literal text in
    WCVP (`"Fl. Carol."` is a citation, not a quoted field), so quoting is
    off, and the row count is checked against the file's lines so a value
    that ever did swallow a delimiter fails here rather than shifting columns.
    """
    import pyarrow.csv as csv

    with zipfile.ZipFile(path) as archive:
        with archive.open(member) as stream:
            lines = sum(1 for _ in stream) - 1
        with archive.open(member) as stream:
            reader = csv.open_csv(
                stream,
                read_options=csv.ReadOptions(block_size=16 << 20),
                parse_options=csv.ParseOptions(delimiter="|", quote_char=False),
                convert_options=csv.ConvertOptions(
                    include_columns=list(columns), strings_can_be_null=True,
                    column_types=column_types(columns),
                ),
            )
            table = pa.Table.from_batches(list(reader))
    if table.num_rows != lines:
        raise ValueError(f"{member}: parsed {table.num_rows} rows from {lines} lines")
    missing = set(columns) - set(table.schema.names)
    if missing:
        raise ValueError(f"{member} is missing {sorted(missing)}; the release changed its schema")
    return table.select(list(columns))
