#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Taipei's designated monuments (古蹟) and registered historic buildings (歷史建築).

Source: the national heritage register of the Bureau of Cultural Heritage,
Ministry of Culture (文化部文化資產局), published as two JSON files under the
Taiwan Open Government Data License v1 and catalogued on `data.gov.tw`:

| file                    | dataset | rows (all Taiwan) | in Taipei |
|-------------------------|---------|------------------:|----------:|
| `assetsCase/1.1.json`   | 6246    |             1,064 |       209 |
| `assetsCase/1.2.json`   | 6965    |             1,789 |       350 |

This is the runbook's first-preference source, an official designation
registry read live, and it is the *national* one rather than the city's own
`臺北市文化資產` list on `data.taipei` because the city's carries no position:
name, class and district only.  The bureau's files carry `latitude` and
`longitude` on every Taipei row but one, plus the class (直轄市定古蹟, 國定古蹟,
歷史建築), the kind (寺廟, 宅第, 城郭, ...) and the current address.

**Filtered to Taipei by the address's `cityName`**, which the register fills
in for every case; a case is kept when any of its addresses is in 臺北市.
`caseId` is the register's own case number and is unique across both files.
"""

import json
import ssl
import sys
import urllib.request
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import UpstreamUnavailable, _retry, emit, make_point_wkt

MONUMENTS = "https://data.boch.gov.tw/opendata/v2/assetsCase/1.1.json"
HISTORIC_BUILDINGS = "https://data.boch.gov.tw/opendata/v2/assetsCase/1.2.json"
CITY = "臺北市"


def fetch_json(url: str, timeout: int = 180):
    """GET a JSON document from the bureau, with the retry policy of
    `get_json_with_retry` and one deliberate difference in TLS verification.

    `data.boch.gov.tw` serves a certificate whose chain is valid and whose
    intermediate lacks a Subject Key Identifier extension.  Python 3.13 turned
    on OpenSSL's `X509_V_FLAG_X509_STRICT` by default, which rejects exactly
    that (`certificate verify failed: Missing Subject Key Identifier`), while
    every browser and curl accept it.  This clears the *strict* flag only:
    the chain is still verified against the system roots and the hostname is
    still checked, so a wrong or expired certificate still fails.
    """
    context = ssl.create_default_context()
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT

    def attempt():
        request = urllib.request.Request(url, headers={"User-Agent": "arborary-ingest/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                if response.status >= 500:
                    raise UpstreamUnavailable(f"HTTP {response.status}")
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 or exc.code == 429:
                raise UpstreamUnavailable(f"HTTP {exc.code}") from exc
            raise
        except (urllib.error.URLError, TimeoutError, ssl.SSLError, OSError) as exc:
            raise UpstreamUnavailable(str(exc)) from exc
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise UpstreamUnavailable(f"non-JSON body: {body[:80]!r}") from exc

    return _retry(attempt, url=url, what="fetch JSON from", max_retries=5, backoff=2.0)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def read_register(url: str, default_class: str, label: str) -> list[dict]:
    rows = fetch_json(url)
    if not isinstance(rows, list):
        raise RuntimeError(f"{label}: expected a JSON list, got {type(rows).__name__}")
    records: list[dict] = []
    seen: set[str] = set()
    unusable = 0
    for row in rows:
        addresses = row.get("addresses") or []
        if not any(a.get("cityName") == CITY for a in addresses):
            continue
        case_id = clean(row.get("caseId"))
        name = clean(row.get("caseName"))
        wkt = make_point_wkt(row.get("longitude"), row.get("latitude"))
        if case_id is None or name is None or wkt is None or case_id in seen:
            unusable += 1
            continue
        seen.add(case_id)
        local = next((a for a in addresses if a.get("cityName") == CITY), {})
        kinds = [clean(t.get("name")) for t in (row.get("assetsTypes") or [])]
        records.append(
            {
                "landmark_id": f"tpe-{case_id}",
                "name": name,
                "geometry_raw": wkt,
                "address": clean(f"{local.get('distName') or ''}{local.get('address') or ''}"),
                "category": clean(row.get("assetsClassifyName")) or default_class,
                "property_type": "、".join(k for k in kinds if k) or None,
                "borough": clean(local.get("distName")),
            }
        )
    print(
        f"{label}: {len(rows)} case(s) nationally, {len(records)} in Taipei kept, "
        f"{unusable} Taipei case(s) dropped (no id, name or position, or a repeat)",
        file=sys.stderr,
    )
    return records


def transform(records: list[dict]) -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array([r["landmark_id"] for r in records], type=pa.string()),
            "city": pa.array(["TWTPE"] * len(records), type=pa.string()),
            "name": pa.array([r["name"] for r in records], type=pa.string()),
            "geometry_raw": pa.array([r["geometry_raw"] for r in records], type=pa.string()),
            "address": pa.array([r["address"] for r in records], type=pa.string()),
            "category": pa.array([r["category"] for r in records], type=pa.string()),
            "property_type": pa.array([r["property_type"] for r in records], type=pa.string()),
            "borough": pa.array([r["borough"] for r in records], type=pa.string()),
        }
    )


if __name__ == "__main__":
    records = read_register(MONUMENTS, "古蹟", "Taiwan heritage register (monuments)")
    records += read_register(HISTORIC_BUILDINGS, "歷史建築", "Taiwan heritage register (historic buildings)")
    emit(transform(records))
