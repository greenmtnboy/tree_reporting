"""
Shared helpers for city ingest scripts (tree_info.py / landmarks.py).

NOT a uv inline script — this is a regular importable module.
All scripts that import it declare pyarrow, requests, and pytrilogy as their own dependencies.

Usage in each city script:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _ingest_shared import emit, normalize_species, ...
"""

import io
import math
import struct
import sys
import time
from datetime import date

import pyarrow as pa
import requests
from trilogy.io.arrow import emit_arrow as emit  # noqa: F401  (re-exported)


# ---------------------------------------------------------------------------
# Species normalisation
# ---------------------------------------------------------------------------

def normalize_species(s: str | None) -> str | None:
    """Capitalize first word, lowercase the rest.  Strip '::' or ' - ' suffixes.

    Returns None for blank / None input.

    Examples:
        "platanus x hispanica"          -> "Platanus x hispanica"
        "Platanus :: London Plane"      -> "Platanus"
        "Quercus robur - English Oak"   -> "Quercus robur"
    """
    if not s or not s.strip():
        return None
    # Strip any common-name suffix separated by "::" or " - "
    for sep in ("::", " - "):
        if sep in s:
            s = s.split(sep)[0]
    parts = s.strip().split()
    if not parts:
        return None
    return " ".join([parts[0].capitalize()] + [p.lower() for p in parts[1:]])


def normalize_species_parts(genus: str | None, epithet: str | None) -> str | None:
    """Combine genus + epithet into a normalised scientific name.

    Genus is capitalised; epithet is lowercased.  Returns None when genus is
    absent or blank (epithet-only is meaningless).

    Examples:
        ("Platanus", "hispanica") -> "Platanus hispanica"
        ("QUERCUS", "ROBUR")     -> "Quercus robur"
        (None, "robur")          -> None
        ("Quercus", None)        -> "Quercus"
    """
    g = (genus or "").strip()
    e = (epithet or "").strip()
    if not g:
        return None
    parts = [g.capitalize()]
    if e:
        parts.append(e.lower())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Coordinate validation
# ---------------------------------------------------------------------------

def validate_coordinates(
    table: pa.Table,
    city: str = "",
    threshold: float = 0.10,
) -> None:
    """Raise ValueError if the table has 0 rows or >threshold null lat/lon.

    Parameters
    ----------
    table:     The Arrow table to validate.
    city:      Optional city name used in error messages.
    threshold: Maximum allowed null fraction for latitude/longitude (default 10%).
    """
    n = table.num_rows
    prefix = f"{city} ingest" if city else "Ingest"
    if n == 0:
        raise ValueError(f"{prefix} produced 0 rows")
    for col in ("latitude", "longitude"):
        null_count = table.column(col).null_count
        if null_count == n:
            raise ValueError(
                f"{prefix}: '{col}' is NULL for all {n} rows — "
                "coordinate extraction failed"
            )
        null_pct = null_count / n
        if null_pct > threshold:
            raise ValueError(
                f"{prefix}: '{col}' is NULL for {null_pct:.0%} of rows "
                f"({null_count}/{n})"
            )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def get_with_retry(
    url: str,
    timeout: int = 120,
    max_retries: int = 5,
    backoff: float = 2.0,
) -> requests.Response:
    """GET with exponential backoff on 5xx / connection errors.

    4xx errors (except 429 Too Many Requests) are not retried — they indicate
    a client-side problem (auth, forbidden, not found) that retrying won't fix.
    """
    err = ""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code < 400:
                return r
            if 400 <= r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()  # raises immediately, no retry
            err = f"HTTP {r.status_code}"
        except requests.exceptions.HTTPError:
            raise  # 4xx non-429: propagate immediately
        except requests.exceptions.RequestException as e:
            err = str(e)
        if attempt < max_retries - 1:
            wait = backoff * (2 ** attempt)
            print(
                f"[retry {attempt + 1}/{max_retries}] {err}, waiting {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} attempts: {err}")


def post_with_retry(
    url: str,
    data: dict,
    timeout: int = 240,
    max_retries: int = 5,
    backoff: float = 10.0,
) -> requests.Response:
    """POST with exponential backoff on 5xx / connection errors.

    Longer default backoff than get_with_retry — suited to Overpass API.
    4xx errors (except 429) are not retried.
    """
    err = ""
    for attempt in range(max_retries):
        try:
            r = requests.post(url, data=data, timeout=timeout)
            if r.status_code < 400:
                return r
            if 400 <= r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()  # raises immediately, no retry
            err = f"HTTP {r.status_code}"
        except requests.exceptions.HTTPError:
            raise  # 4xx non-429: propagate immediately
        except requests.exceptions.RequestException as e:
            err = str(e)
        if attempt < max_retries - 1:
            wait = backoff * (2 ** attempt)
            print(
                f"[retry {attempt + 1}/{max_retries}] {err}, waiting {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise RuntimeError(f"Failed to POST {url} after {max_retries} attempts: {err}")


def download_parquet(url: str, timeout: int = 300) -> io.BytesIO:
    """Stream-download a parquet file into a BytesIO buffer and return it seeked to 0."""
    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# WKB / WKT geometry helpers
# ---------------------------------------------------------------------------

def parse_wkb_point(wkb: bytes | None) -> tuple[float | None, float | None]:
    """Parse a WKB binary Point into (lon, lat).

    OpenDataSoft exports geo_point_2d as WKB:
      byte 0   : byte order (1 = little-endian, 0 = big-endian)
      bytes 1-4: geometry type (uint32, value 1 = Point)
      bytes 5-12: x (double) = longitude
      bytes 13-20: y (double) = latitude

    Returns (None, None) for None or too-short input.
    """
    if wkb is None or len(wkb) < 21:
        return None, None
    bo = "<" if wkb[0] == 1 else ">"
    x, y = struct.unpack_from(bo + "dd", wkb, 5)
    return x, y


def make_point_wkt(lon, lat) -> str | None:
    """Return a WKT POINT string or None if either coordinate is None."""
    if lon is None or lat is None:
        return None
    return f"POINT({lon} {lat})"


# ---------------------------------------------------------------------------
# RD New (EPSG:28992) → WGS84
# ---------------------------------------------------------------------------

def rd_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert RD New (x, y) to (lat, lon) in WGS84.

    Polynomial approximation (~1 m accuracy).
    Coefficients from the Dutch Kadaster / RDNAPTRANS reference.
    """
    x0, y0 = 155000.0, 463000.0
    phi0, lam0 = 52.15517440, 5.38720621

    dx = (x - x0) * 1e-5
    dy = (y - y0) * 1e-5

    coefs_phi = [
        (0, 1, 3235.65389),
        (2, 0, -32.58297),
        (0, 2, -0.24750),
        (2, 1, -0.84978),
        (0, 3, -0.06550),
        (2, 2, -0.01709),
        (1, 0, -0.00738),
        (4, 0, 0.00530),
        (2, 3, -0.00039),
        (4, 1, 0.00033),
        (1, 1, -0.00012),
    ]
    coefs_lam = [
        (1, 0, 5260.52916),
        (1, 1, 105.94684),
        (1, 2, 2.45656),
        (3, 0, -0.81885),
        (1, 3, 0.05594),
        (3, 1, -0.05607),
        (0, 1, 0.01199),
        (3, 2, -0.00256),
        (1, 4, 0.00128),
        (0, 2, 0.00022),
        (2, 0, -0.00022),
        (5, 0, 0.00026),
    ]

    dphi = sum(c * (dx ** p) * (dy ** q) for p, q, c in coefs_phi)
    dlam = sum(c * (dx ** p) * (dy ** q) for p, q, c in coefs_lam)

    lat = phi0 + dphi / 3600.0
    lon = lam0 + dlam / 3600.0
    return lat, lon


def rd_centroid(ring: list) -> tuple[float, float]:
    """Return (mean_x, mean_y) of a coordinate ring (RD New)."""
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# ---------------------------------------------------------------------------
# Plant date helpers
# ---------------------------------------------------------------------------

def parse_plant_date_year(year) -> date | None:
    """Convert an integer or string year to January 1 of that year.

    Returns None for None, 0, negative values, values > 2100, or
    non-numeric input.
    """
    if year is None:
        return None
    try:
        y = int(year)
    except (ValueError, TypeError):
        return None
    if y <= 0 or y > 2100:
        return None
    return date(y, 1, 1)


# ---------------------------------------------------------------------------
# DBH / dimension conversion helpers
# ---------------------------------------------------------------------------

def circumference_cm_to_dbh_inches(circ_cm) -> float | None:
    """Convert trunk circumference in cm to diameter at breast height in inches.

    DBH = circumference / π, then convert cm → inches (÷ 2.54).
    Returns None for None or zero input.
    """
    if circ_cm is None:
        return None
    try:
        v = float(circ_cm)
    except (ValueError, TypeError):
        return None
    if v == 0:
        return None
    return v / (math.pi * 2.54)


def cm_to_inches(cm) -> float | None:
    """Convert a centimetre value to inches (÷ 2.54).  Returns None for None."""
    if cm is None:
        return None
    try:
        return float(cm) / 2.54
    except (ValueError, TypeError):
        return None
