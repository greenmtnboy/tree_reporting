#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests"]
# ///

"""
Fetch Paris historic monuments from data.iledefrance.fr and emit Arrow IPC to stdout.

Source: https://data.iledefrance.fr/explore/dataset/immeubles-proteges-au-titre-des-monuments-historiques/
Dataset: Immeubles protégés au titre des Monuments Historiques (Île-de-France)

Filter: department 75 (Paris) only — ~1,885 records.

Field mapping:
  reference                          -> landmark_id  (prefixed "frpar-")
  titre_editorial_de_la_notice       -> name
  coordonnees_au_format_wgs84        -> geometry_raw (WKT POINT), latitude, longitude
  commune_forme_editoriale           -> arrondissement
  date_et_typologie_de_la_protection -> protection_type
  denomination_de_l_edifice          -> denomination
"""

import io
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import requests

DATASET_URL = (
    "https://data.iledefrance.fr/api/explore/v2.1/catalog/datasets/"
    "immeubles-proteges-au-titre-des-monuments-historiques/exports/parquet"
    "?where=departement_format_numerique%3D%2275%22"
    "&select=reference%2Ctitre_editorial_de_la_notice%2Cadresse_forme_editoriale"
    "%2Ccommune_forme_editoriale%2Cdate_et_typologie_de_la_protection"
    "%2Cdenomination_de_l_edifice%2Ccoordonnees_au_format_wgs84"
    "&lang=en&timezone=UTC"
)


def download_parquet() -> io.BytesIO:
    r = requests.get(DATASET_URL, stream=True, timeout=180)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


def transform(table: pa.Table) -> pa.Table:
    names = table.schema.names

    # --- landmark_id: prefix reference with "frpar-" ---
    ref_col = next((c for c in names if c.lower() == "reference"), None)
    refs = table[ref_col].to_pylist() if ref_col else [None] * table.num_rows
    landmark_id = pa.array(
        [f"frpar-{v}" if v is not None else None for v in refs],
        type=pa.string(),
    )

    # --- name: titre_editorial_de_la_notice ---
    name_col = next((c for c in names if c.lower() == "titre_editorial_de_la_notice"), None)
    name = table[name_col] if name_col else pa.array([None] * table.num_rows, type=pa.string())

    # --- lat/lon + geometry_raw from coordonnees_au_format_wgs84 (WKB binary) ---
    import struct
    coord_col = next((c for c in names if c.lower() == "coordonnees_au_format_wgs84"), None)
    if coord_col is not None:
        lon_list, lat_list = [], []
        for wkb in table[coord_col].to_pylist():
            if wkb is None or len(wkb) < 21:
                lon_list.append(None); lat_list.append(None)
                continue
            bo = '<' if wkb[0] == 1 else '>'
            x, y = struct.unpack_from(bo + 'dd', wkb, 5)
            lon_list.append(x); lat_list.append(y)
    else:
        n = table.num_rows
        lat_list = [None] * n
        lon_list = [None] * n

    lat = pa.array(lat_list, type=pa.float64())
    lon = pa.array(lon_list, type=pa.float64())
    geometry_raw = pa.array(
        [
            f"POINT({lo} {la})" if lo is not None and la is not None else None
            for lo, la in zip(lon_list, lat_list)
        ],
        type=pa.string(),
    )

    # --- Paris-specific fields ---
    arrond_col = next((c for c in names if c.lower() == "commune_forme_editoriale"), None)
    arrondissement = (
        table[arrond_col] if arrond_col
        else pa.array([None] * table.num_rows, type=pa.string())
    )

    prot_col = next((c for c in names if c.lower() == "date_et_typologie_de_la_protection"), None)
    protection_type = (
        table[prot_col] if prot_col
        else pa.array([None] * table.num_rows, type=pa.string())
    )

    denom_col = next((c for c in names if c.lower() == "denomination_de_l_edifice"), None)
    denomination = (
        table[denom_col] if denom_col
        else pa.array([None] * table.num_rows, type=pa.string())
    )

    n = table.num_rows
    return pa.table(
        {
            "landmark_id": landmark_id,
            "city": pa.array(["FRPAR"] * n, type=pa.string()),
            "name": name,
            "geometry_raw": geometry_raw,
            "latitude": lat,
            "longitude": lon,
            "arrondissement": arrondissement,
            "protection_type": protection_type,
            "denomination": denomination,
        }
    )


def validate(table: pa.Table) -> None:
    n = table.num_rows
    if n == 0:
        raise ValueError("Paris landmarks ingest produced 0 rows")
    for col in ("latitude", "longitude"):
        null_count = table.column(col).null_count
        if null_count / n > 0.10:
            raise ValueError(f"Paris landmarks: '{col}' has {null_count}/{n} NULL rows ({null_count/n:.1%}) — exceeds 10% threshold")


def emit(table: pa.Table) -> None:
    with pa.ipc.new_stream(sys.stdout.buffer, table.schema) as writer:
        writer.write_table(table)


if __name__ == "__main__":
    buf = download_parquet()
    raw = pq.read_table(buf)
    table = transform(raw)
    validate(table)
    emit(table)
