from __future__ import annotations

import json
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.feedback import (
    finalize_registration_feedback,
    load_persisted_reviews,
    persist_review_payload,
)

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024


def serve_registration_review(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    review_dir: str | Path | None = None,
    bind: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    raster = Path(raster_path).resolve()
    directory = (
        Path(review_dir).resolve()
        if review_dir is not None
        else (config.paths.root / "qa" / "registration" / raster.stem).resolve()
    )
    if not (directory / "index.html").exists():
        raise FileNotFoundError(
            f"registration UI does not exist at {directory}; run qa registration first"
        )

    class ReviewHandler(SimpleHTTPRequestHandler):
        def _json_response(self, status: HTTPStatus, payload: object) -> None:
            encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _read_payload(self) -> dict[str, object]:
            raw_length = self.headers.get("Content-Length")
            if raw_length is None:
                raise ValueError("request must include Content-Length")
            length = int(raw_length)
            if length < 0 or length > _MAX_REVIEW_PAYLOAD_BYTES:
                raise ValueError("review payload is too large")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("review payload must be a JSON object")
            return payload

        def do_GET(self) -> None:  # noqa: N802
            if urlsplit(self.path).path == "/api/reviews":
                try:
                    self._json_response(HTTPStatus.OK, load_persisted_reviews(directory))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            super().do_GET()

        def do_PUT(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/reviews":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                result = persist_review_payload(directory, self._read_payload())
                self._json_response(HTTPStatus.OK, result)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/finalize":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                result = finalize_registration_feedback(
                    config,
                    raster,
                    review_dir=directory,
                )
                self._json_response(HTTPStatus.OK, result)
            except (OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    handler = partial(ReviewHandler, directory=str(directory))
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    print(f"Registration QA: http://{bind}:{port}/", flush=True)
    print("Reviews auto-save to reviews.json; Ctrl+C stops the server.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
