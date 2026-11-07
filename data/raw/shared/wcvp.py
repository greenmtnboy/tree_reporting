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
# What `refresh-wcvp` publishes (`wcvp_info.preql`), `{table}` being `names`
# or `distribution`; the version is the model's `data_version`.
PUBLISHED_URL = (
    "https://storage.googleapis.com/trilogy_public_models/duckdb/trees/"
    "wcvp_{table}_v2.parquet"
)
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


def names_table(path: Path) -> pa.Table:
    """wcvp_names.csv plus `name_key` and `accepted_taxon_name`.

    The accepted record's name is joined on here so a consumer resolving a
    synonym needs no self-join.
    """
    import pyarrow.compute as pc

    table = read_member(path, NAMES_MEMBER, NAME_COLUMNS)
    names = table.column("taxon_name")
    index = pc.index_in(table.column("accepted_plant_name_id"), value_set=table.column("plant_name_id"))
    table = table.append_column("name_key", name_key_array(names))
    return table.append_column("accepted_taxon_name", pc.take(names, index))


def distribution_table(path: Path) -> pa.Table:
    """wcvp_distribution.csv with its 0/1 flags as booleans."""
    import pyarrow.compute as pc

    table = read_member(path, DISTRIBUTION_MEMBER, DISTRIBUTION_COLUMNS)
    for flag in DISTRIBUTION_FLAGS:
        idx = table.schema.get_field_index(flag)
        table = table.set_column(idx, flag, pc.equal(table.column(flag), 1))
    return table


class ChecklistUnavailable(RuntimeError):
    """The published parquets (or the local copy) could not be read."""


# What a reader holds of each name record; the bibliographic columns are left
# in the parquet.
RECORD_COLUMNS = (
    "name_key", "plant_name_id", "accepted_plant_name_id", "accepted_taxon_name",
    "taxon_status", "taxon_rank", "family", "taxon_name", "taxon_authors",
    "geographic_area", "lifeform_description", "climate_description",
)
AREA_COLUMNS = (
    "plant_name_id", "continent", "region", "area", "area_code_l3",
    "introduced", "extinct", "location_doubtful",
)


class Checklist:
    """Exact lookups against WCVP, for the species audit and the enrichment job.

    Reads the parquets `refresh-wcvp` publishes, or a local `wcvp.zip`
    (`source`), which is how the tests and a workstation ahead of the job read
    it.  A `.parquet` source replaces only the names table.  Nothing holds the
    1.45M-row table: `prefetch` loads the records for the names asked about in
    one query, and `prefetch_taxa` the accepted records and their areas, so a
    sweep or an enrichment run costs a few scans however many names it has.
    """

    def __init__(self, source: str | None = None) -> None:
        self.source = source
        self.by_key: dict[str, list[dict]] = {}
        self.by_id: dict[int, dict] = {}
        self.areas: dict[int, list[dict]] = {}
        self._loaded_keys: set[str] = set()
        self._loaded_ids: set[int] = set()
        self._con = None

    # -- reading -----------------------------------------------------------

    def _connection(self):
        if self._con is not None:
            return self._con
        import duckdb

        con = duckdb.connect()
        source = self.source
        try:
            if source and source.endswith(".zip"):
                con.register("wcvp_names", names_table(Path(source)))
                con.register("wcvp_distribution", distribution_table(Path(source)))
            else:
                con.execute("install httpfs; load httpfs;")
                names = source or PUBLISHED_URL.format(table="names")
                areas = PUBLISHED_URL.format(table="distribution")
                con.execute(f"create view wcvp_names as select * from read_parquet('{names}')")
                con.execute(f"create view wcvp_distribution as select * from read_parquet('{areas}')")
                con.execute("select 1 from wcvp_names limit 1")
        except Exception as e:
            raise ChecklistUnavailable(
                f"cannot read WCVP from {source or PUBLISHED_URL.format(table='names')} ({e}); "
                f"run the refresh-wcvp job, or use a local copy of Kew's {WCVP_ZIP_URL}"
            ) from e
        self._con = con
        return con

    def _select(self, columns: tuple[str, ...], table: str, column: str, values: list) -> list[dict]:
        con = self._connection()
        rows = con.execute(
            f"select {', '.join(columns)} from {table} where {column} in (select unnest(?))",
            [values],
        ).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def prefetch(self, names) -> None:
        keys = sorted({k for k in (name_key(n) for n in names) if k} - self._loaded_keys)
        if not keys:
            return
        for record in self._select(RECORD_COLUMNS, "wcvp_names", "name_key", keys):
            self.by_key.setdefault(record["name_key"], []).append(record)
            self.by_id[record["plant_name_id"]] = record
        self._loaded_keys.update(keys)

    def prefetch_taxa(self, ids) -> None:
        """The records and areas of these plant_name_ids (accepted taxa)."""
        wanted = sorted({i for i in ids if i is not None} - self._loaded_ids)
        if not wanted:
            return
        for record in self._select(RECORD_COLUMNS, "wcvp_names", "plant_name_id", wanted):
            self.by_id[record["plant_name_id"]] = record
        for area in self._select(AREA_COLUMNS, "wcvp_distribution", "plant_name_id", wanted):
            self.areas.setdefault(area["plant_name_id"], []).append(area)
        self._loaded_ids.update(wanted)

    # -- answers -----------------------------------------------------------

    def records(self, name: str) -> list[dict]:
        """Every record that *is* this name, misapplied ones skipped.

        "sensu auct." says a name was used wrongly, not what it means, and
        `Platanus occidentalis` carries one beside its accepted record.
        """
        self.prefetch([name])
        return [r for r in self.by_key.get(name_key(name), []) if r["taxon_status"] != "Misapplied"]

    def misapplied(self, name: str) -> list[dict]:
        """The records saying this name has been used for another taxon."""
        self.prefetch([name])
        return [r for r in self.by_key.get(name_key(name), []) if r["taxon_status"] == "Misapplied"]

    def lookup(self, name: str) -> dict:
        """`{'status': accepted|synonym|unplaced|not_found|ambiguous}`.

        `not_found` means no WCVP record *is* this name.  A record whose
        accepted id is itself is accepted; one pointing at another record is a
        synonym of that name (Kew's Synonym, Orthographic, Illegitimate and
        Invalid all point that way); one pointing nowhere is unplaced.
        **Every** exact record is read: `Quercus lyrata` is Walter's accepted
        overcup oak and Spreng.'s synonym of `Quercus lobata`, and taking
        whichever came first would have relabelled 2,773 trees.  Disagreement
        is `ambiguous`, never resolved by picking one.
        """
        records = self.records(name)
        if not records:
            return {"status": "not_found", "accepted": None, "matched": None, "family": None}
        readings = set()
        for r in records:
            own, accepted_id = r["plant_name_id"], r["accepted_plant_name_id"]
            if accepted_id == own:
                readings.add(("accepted", None))
            elif accepted_id is not None and r["accepted_taxon_name"]:
                readings.add(("synonym", r["accepted_taxon_name"]))
            else:
                readings.add(("unplaced", None))
        matched, family = records[0]["taxon_name"], records[0]["family"]
        if len(readings) > 1:
            return {
                "status": "ambiguous", "accepted": None, "matched": matched, "family": family,
                "readings": sorted(f"{s}:{a or '-'}" for s, a in readings),
            }
        status, accepted = readings.pop()
        return {"status": status, "accepted": accepted, "matched": matched, "family": family}
