from __future__ import annotations

import json
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.feedback import (
    finalize_registration_feedback,
    load_persisted_reviews,
    persist_review_payload,
    snapshot_registration_annotations,
)
from urban_tree_ml.model_debug import (
    MODEL_DEBUG_HTML,
    ModelDebugBundle,
    inject_studio_navigation,
    render_studio_home,
)

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024


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
    model_bundle = None
    if evaluation_dir is not None or (selected_evaluation_dir / "metrics.json").exists():
        model_bundle = ModelDebugBundle(config, selected_evaluation_dir, raster)
    registration_html = inject_studio_navigation(
        (directory / "index.html").read_text(encoding="utf-8")
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
            path = urlsplit(self.path).path
            if path in {"/", "/index.html"}:
                self._html_response(render_studio_home(model_bundle is not None))
                return
            if path in {"/registration", "/registration/"}:
                self._html_response(registration_html)
                return
            if path in {"/model", "/model/"}:
                self._html_response(MODEL_DEBUG_HTML)
                return
            if path == "/api/reviews":
                try:
                    self._json_response(HTTPStatus.OK, load_persisted_reviews(directory))
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if path == "/api/model/summary":
                if model_bundle is None:
                    self._json_response(
                        HTTPStatus.NOT_FOUND,
                        {"error": "no validation evaluation is loaded"},
                    )
                else:
                    self._json_response(HTTPStatus.OK, model_bundle.summary())
                return
            if path.startswith("/api/model/chip/"):
                if model_bundle is None:
                    self._json_response(
                        HTTPStatus.NOT_FOUND,
                        {"error": "no validation evaluation is loaded"},
                    )
                    return
                chip_id = unquote(path.removeprefix("/api/model/chip/"))
                try:
                    self._json_response(HTTPStatus.OK, model_bundle.chip(chip_id))
                except KeyError as error:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            if path.startswith("/api/model/image/") and path.endswith(".png"):
                if model_bundle is None:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                chip_id = unquote(path.removeprefix("/api/model/image/").removesuffix(".png"))
                try:
                    self._content_response(
                        HTTPStatus.OK,
                        model_bundle.chip_image(chip_id),
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
