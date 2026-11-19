#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy>=0.3.391", "pydantic", "google-cloud-storage"]
# ///
"""Localhost admin UI for hand-editing the species enrichment parquet.

The enrichment table (`tree_enrichment_v{n}.parquet` in GCS) is the one row
per species that every tree row joins to: its common name, growth form, traits,
native range and photo.  It is written by an LLM run, and the LLM is sometimes
wrong -- a photo of a different plant, a description of the wrong taxon, a
trait that reads as off.  `enrichment/backfill.py` can re-ask the model, but a
reviewer who already knows the answer should be able to just write it down.

This serves a small form over the table.  The form is only a front end: the
checks, the staging and the publish live in `backend.py`, which `cli.py`
drives from the command line too.  Edits are staged locally and land in GCS
only on an explicit publish, which:

  1. re-reads the *current* published table rather than uploading the copy this
     process loaded -- the daily `refresh-enrichment` job may have appended new
     species in the meantime, and uploading a stale snapshot would drop them;
  2. patches the staged rows into it, and the same values into every alias
     row of the taxon -- its hybrid-mark twin and each name in `synonyms`
     (see `with_species_aliases`); and
  3. writes a local parquet, uploads it, and reads it back to verify, exactly
     the way `tree_enrichment.py --limit` does.

An edited row carries today's `enriched_at`, which is what keeps the daily job
from re-enriching it: the retry window in `_tree_shared.REENRICH_INCOMPLETE_BEFORE`
is a fixed date, and a row stamped after it is left alone.  The row must still
clear `ENRICHMENT_COMPLETE_SQL` (a common name and a growth form) or the
freshness probe would report the table stale on every tick, so the form
refuses to save without them.

Sentinel rows ("Unknown", "Palm", ...) are authored in `shared.ingest` and
re-appended on every run, so they cannot be edited here; fix them in code.

Synonyms
--------
`synonyms` lists the other scientific names of the taxon.  Adding a name there
is how a reviewer folds a duplicate row into this one: on publish the
duplicate's row is overwritten with this row's values (its own `synonyms` then
pointing back here), and the two stay in step from then on whichever is
edited.  That bridges the join for tree rows still carrying the old name; it
does not change what the ingest publishes.  For that the pair goes into
`SPECIES_SYNONYMS` in `shared/ingest.py`, after which the synonym's row is an
alias the daily job maintains and the form shows read-only.

A duplicate that is not a synonym but a *misspelling* -- `Acer platenoides`,
which POWO cannot match at all -- takes the same two steps and lands in
`SPECIES_MISSPELLINGS` instead.  The difference is only in what the pair
claims: a misspelling never appears in the accepted row's `synonyms`, because
that column says what else the taxon is called and a typo is not one of its
names.  The form treats both the same way, read-only and pointing here.

Run
---
    gcloud auth application-default login        # once
    cd data/raw && uv run enrichment/admin/server.py

then open http://127.0.0.1:4175.  The server binds to localhost only.  Set
ENRICHMENT_ADMIN_PORT to change the port.

The species table is a public object, so browsing needs no credentials; only
publish does.  The website (arborary.world) picks the change up on its next
build -- a `workflow_dispatch` of its deploy workflow republishes with fresh
data -- and the map on the next `refresh-enrichment` tick is unaffected, since
the browser reads the parquet directly.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from enrichment.admin.backend import (  # noqa: E402
    GROUPS,
    HTML,
    AdminState,
    ValidationError,
    _json_default,
    field_specs,
    inat_candidates,
    rank_photos,
)

# ── HTTP ───────────────────────────────────────────────────────────────────────

STATE = AdminState()


class Handler(BaseHTTPRequestHandler):
    server_version = "enrichment-admin/1.0"

    def log_message(self, fmt, *args):  # quieter than the default
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    # -- helpers -------------------------------------------------------------------

    def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str, problems: list[str] | None = None) -> None:
        self._json({"error": message, "problems": problems or [message]}, status)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _route(self) -> tuple[str, list[str], dict[str, str]]:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        return url.path, parts, query

    # -- verbs -----------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path, parts, q = self._route()
        try:
            if path == "/" or path == "/index.html":
                body = HTML.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parts == ["api", "status"]:
                self._json(STATE.status())
            elif parts == ["api", "schema"]:
                self._json({"fields": field_specs(), "groups": GROUPS})
            elif parts == ["api", "ecoregions"]:
                self._json(STATE.ecoregions())
            elif parts == ["api", "species"]:
                self._json(STATE.search(q.get("q", ""), q.get("filter", ""), int(q.get("limit", 200)), q.get("sort", "trees")))
            elif len(parts) == 3 and parts[:2] == ["api", "species"]:
                detail = STATE.detail(parts[2])
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, f"no species {parts[2]!r}")
                else:
                    self._json(detail)
            elif parts == ["api", "inat"]:
                self._json(inat_candidates(
                    q.get("species", ""),
                    page=max(1, int(q.get("page", 1))),
                    taxon_id=int(q["taxon_id"]) if q.get("taxon_id") else None,
                    query=q.get("taxon_q") or None,
                    rank=q.get("rank") or None,
                ))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "species"]:
                self._json(STATE.save(parts[2], self._body()))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValidationError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid", exc.problems)
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if len(parts) == 4 and parts[:2] == ["api", "species"] and parts[3] == "edit":
                STATE.discard(parts[2])
                self._json(STATE.detail(parts[2]) or {})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if parts == ["api", "publish"]:
                self._json(STATE.publish())
            elif parts == ["api", "reload"]:
                if self._body().get("discard"):
                    STATE.discard_all()
                STATE.load()
                self._json(STATE.status())
            elif parts == ["api", "rank"]:
                self._json(rank_photos(self._body().get("urls") or []))
            elif parts == ["api", "recount"]:
                STATE.load_counts()
                self._json(STATE.status())
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValidationError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid", exc.problems)
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("ENRICHMENT_ADMIN_PORT", 4175)))
    args = parser.parse_args()

    STATE.load()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[admin] http://127.0.0.1:{args.port}  (localhost only; Ctrl-C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
