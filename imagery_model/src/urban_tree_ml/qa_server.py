from __future__ import annotations

import json
import os
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.feedback import (
    finalize_registration_feedback,
    load_persisted_reviews,
    persist_review_payload,
    snapshot_registration_annotations,
)
from urban_tree_ml.model_debug import (
    CHIP_COMPARE_HTML,
    MODEL_DEBUG_HTML,
    RUN_HISTORY_HTML,
    RunDebugCatalog,
    inject_studio_navigation,
    render_studio_home,
)
from urban_tree_ml.quality import append_validation_chip_to_registration_review

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024


def _inject_street_view_embed_key(html: str, api_key: str | None) -> str:
    if not api_key:
        return html
    encoded_key = json.dumps(api_key).replace("<", "\\u003c")
    return html.replace(
        "const streetViewEmbedApiKey = null;",
        f"const streetViewEmbedApiKey = {encoded_key};",
        1,
    )


def serve_registration_review(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    review_dir: str | Path | None = None,
    evaluation_dir: str | Path | None = None,
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
    selected_evaluation_dir = (
        Path(evaluation_dir).resolve()
        if evaluation_dir is not None
        else (
            config.paths.root
            / "runs"
            / config.experiment
            / "evaluation"
            / "validation"
        ).resolve()
    )
    run_catalog = RunDebugCatalog(config, selected_evaluation_dir, raster)

    def registration_html() -> str:
        return _inject_street_view_embed_key(
            inject_studio_navigation((directory / "index.html").read_text(encoding="utf-8")),
            os.environ.get("GOOGLE_MAPS_EMBED_API_KEY"),
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

        def _content_response(
            self,
            status: HTTPStatus,
            payload: bytes,
            content_type: str,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _html_response(self, html: str) -> None:
            self._content_response(
                HTTPStatus.OK,
                html.encode("utf-8"),
                "text/html; charset=utf-8",
            )

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
            parsed = urlsplit(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            run_id = query.get("run", [None])[0]
            if path in {"/", "/index.html"}:
                self._html_response(render_studio_home(len(run_catalog) > 0, len(run_catalog)))
                return
            if path in {"/registration", "/registration/"}:
                self._html_response(registration_html())
                return
            if path in {"/runs", "/runs/"}:
                self._html_response(RUN_HISTORY_HTML)
                return
            if path in {"/compare", "/compare/"}:
                self._html_response(CHIP_COMPARE_HTML)
                return
            if path in {"/model", "/model/"}:
                self._html_response(MODEL_DEBUG_HTML)
                return
            if path == "/curate":
                chip_id = query.get("chip", [None])[0]
                if not chip_id:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "chip is required"})
                    return
                try:
                    bundle = run_catalog.bundle(run_id)
                    truth = bundle.ground_truth[bundle.ground_truth["chip_id"] == chip_id].copy()
                    result = append_validation_chip_to_registration_review(
                        config,
                        raster,
                        directory,
                        chip_id,
                        truth,
                    )
                except (KeyError, OSError, ValueError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                location = f"/registration?scene={quote(str(result['scene_id']))}&fullscreen=1"
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", location)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path == "/api/reviews":
                try:
                    self._json_response(HTTPStatus.OK, load_persisted_reviews(directory))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if path == "/api/model/summary":
                try:
                    bundle = run_catalog.bundle(run_id)
                except KeyError:
                    self._json_response(
                        HTTPStatus.NOT_FOUND,
                        {"error": "no validation evaluation is loaded"},
                    )
                    return
                self._json_response(HTTPStatus.OK, bundle.summary())
                return
            if path == "/api/runs":
                self._json_response(HTTPStatus.OK, run_catalog.summary())
                return
            if path.startswith("/api/runs/chip/"):
                chip_id = unquote(path.removeprefix("/api/runs/chip/"))
                try:
                    self._json_response(
                        HTTPStatus.OK,
                        run_catalog.chip_comparison(chip_id),
                    )
                except (KeyError, OSError, ValueError) as error:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            if path.startswith("/api/model/chip/"):
                chip_id = unquote(path.removeprefix("/api/model/chip/"))
                try:
                    self._json_response(HTTPStatus.OK, run_catalog.bundle(run_id).chip(chip_id))
                except KeyError as error:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            if path.startswith("/api/model/image/") and path.endswith(".png"):
                chip_id = unquote(path.removeprefix("/api/model/image/").removesuffix(".png"))
                try:
                    self._content_response(
                        HTTPStatus.OK,
                        run_catalog.bundle(run_id).chip_image(chip_id),
                        "image/png",
                    )
                except KeyError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                return
            super().do_GET()

        def do_PUT(self) -> None:  # noqa: N802
            if urlsplit(self.path).path != "/api/reviews":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                result = persist_review_payload(directory, self._read_payload())
                result.update(
                    snapshot_registration_annotations(
                        config,
                        raster,
                        review_dir=directory,
                    )
                )
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
    print(f"Urban Tree Model Studio: http://{bind}:{port}/", flush=True)
    print(
        "Registration reviews auto-save to reviews.json and the tracked annotation bundle; "
        "Ctrl+C stops the server.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
