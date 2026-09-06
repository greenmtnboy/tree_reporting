from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from urban_tree_ml.config import ProjectConfig, StudioConfig, load_config
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
from urban_tree_ml.quality import (
    append_validation_chip_to_registration_review,
    render_registration_review_html,
)

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024
_CURATION_RETURN_PATHS = frozenset({"/registration", "/runs", "/compare", "/model"})


@dataclass(frozen=True)
class ReviewContext:
    city: str
    label: str
    config: ProjectConfig
    raster: Path
    directory: Path
    evaluation_dir: Path


def _checked_review_context(
    city: str,
    label: str,
    config: ProjectConfig,
    raster_path: str | Path,
    review_dir: str | Path | None,
    evaluation_dir: str | Path | None,
) -> ReviewContext:
    raster = Path(raster_path).resolve()
    directory = (
        Path(review_dir).resolve()
        if review_dir is not None
        else (config.paths.root / "qa" / "registration" / raster.stem).resolve()
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
    if city != config.inventory.city.lower():
        raise ValueError(
            f"studio city {city!r} does not match config inventory city "
            f"{config.inventory.city!r}"
        )
    if not raster.is_file():
        raise FileNotFoundError(f"studio raster does not exist: {raster}")
    if not (directory / "index.html").exists():
        raise FileNotFoundError(
            f"registration UI does not exist at {directory}; run qa registration first"
        )
    return ReviewContext(
        city=city,
        label=label,
        config=config,
        raster=raster,
        directory=directory,
        evaluation_dir=selected_evaluation_dir,
    )


def _safe_curation_return(value: str | None) -> str:
    if not value:
        return "/registration"
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.path not in _CURATION_RETURN_PATHS:
        return "/registration"
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _validation_chip_review_status(review_dir: str | Path) -> dict[str, object]:
    directory = Path(review_dir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    scenes = manifest.get("scenes", [])
    if not isinstance(scenes, list):
        raise ValueError("registration review scenes must be a list")
    scene_reviews = load_persisted_reviews(directory).get("scene_reviews", {})
    if not isinstance(scene_reviews, dict):
        scene_reviews = {}
    chips: dict[str, dict[str, object]] = {}
    for scene in scenes:
        if not isinstance(scene, dict) or not scene.get("validation_chip_id"):
            continue
        scene_id = str(scene["scene_id"])
        chips[str(scene["validation_chip_id"])] = {
            "scene_id": scene_id,
            "reviewed": bool(scene_reviews.get(scene_id, {}).get("done")),
        }
    return {"chips": chips}


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
    context = _checked_review_context(
        config.inventory.city.lower(),
        config.inventory.city,
        config,
        raster_path,
        review_dir,
        evaluation_dir,
    )
    _serve_review_contexts(
        {context.city: context},
        default_city=context.city,
        bind=bind,
        port=port,
    )


def serve_model_studio(
    studio: StudioConfig,
    *,
    bind: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    contexts: dict[str, ReviewContext] = {}
    for city in studio.cities:
        config = load_config(city.config_path)
        contexts[city.city] = _checked_review_context(
            city.city,
            city.label,
            config,
            city.raster,
            city.review_dir,
            city.evaluation_dir,
        )
    roots = {context.config.paths.root for context in contexts.values()}
    if len(roots) != 1:
        raise ValueError("all studio cities must share one artifact root")
    _serve_review_contexts(
        contexts,
        default_city=studio.default_city,
        bind=bind,
        port=port,
    )


def _serve_review_contexts(
    contexts: dict[str, ReviewContext],
    *,
    default_city: str,
    bind: str,
    port: int,
) -> None:
    default_context = contexts[default_city]
    run_catalog = RunDebugCatalog(
        default_context.config,
        default_context.evaluation_dir,
        default_context.raster,
    )

    def context_for_city(city: str | None) -> ReviewContext:
        selected = city or default_city
        if selected not in contexts:
            raise KeyError(f"unknown studio city {selected!r}")
        return contexts[selected]

    def context_for_run(run_id: str | None) -> ReviewContext | None:
        if not run_id:
            return None
        try:
            bundle = run_catalog.bundle(run_id)
        except KeyError:
            return None
        return next(
            (
                context
                for context in contexts.values()
                if context.config.inventory.city == bundle.config.inventory.city
                and context.raster == bundle.raster_path
            ),
            None,
        )

    def context_for_request(
        query: dict[str, list[str]],
        run_id: str | None = None,
    ) -> ReviewContext:
        requested_city = query.get("city", [None])[0]
        city_context = context_for_city(requested_city) if requested_city else None
        run_context = context_for_run(run_id)
        if city_context is not None and run_context is not None and city_context != run_context:
            raise ValueError("requested city does not own the selected validation run")
        return run_context or city_context or default_context

    def run_id_for_context(context: ReviewContext) -> str | None:
        training_run_id = context.evaluation_dir.parent.parent.name
        cohort = context.evaluation_dir.name
        candidate = (
            training_run_id
            if cohort == "validation"
            else f"{training_run_id}::{cohort}"
        )
        available = {str(record["run_id"]) for record in run_catalog.summary()["runs"]}
        return candidate if candidate in available else None

    def city_navigation(html: str, context: ReviewContext) -> str:
        run_id = run_id_for_context(context)
        model_query = f"?{urlencode({'run': run_id})}" if run_id else ""
        html = html.replace(
            'href="/registration"',
            f'href="/registration?{urlencode({"city": context.city})}"',
        )
        html = html.replace(
            'href="/runs"',
            f'href="/runs?{urlencode({"city": context.city})}"',
        )
        html = html.replace('href="/model"', f'href="/model{model_query}"')
        options = "".join(
            f'<option value="{city}"{" selected" if city == context.city else ""}>'
            f"{item.label}</option>"
            for city, item in contexts.items()
        )
        switcher = (
            '<label class="studio-city-switch">City '
            f'<select aria-label="Review city" onchange="location.href=\'/registration?city=\'+'
            f'encodeURIComponent(this.value)">{options}</select></label>'
        )
        style = (
            "<style>.studio-city-switch{display:inline-flex;align-items:center;gap:6px;"
            "margin-left:auto;color:#aabdaf;font-size:13px}.studio-city-switch select{"
            "min-height:32px;color:#edf6ef;background:#203027;border:1px solid #496252;"
            "border-radius:6px;padding:5px 9px}</style>"
        )
        for closing_nav in ("</nav>",):
            if closing_nav in html:
                return html.replace(closing_nav, f"{switcher}</nav>{style}", 1)
        return html.replace("<body>", f"<body><nav class=\"nav\">{switcher}</nav>{style}", 1)

    def registration_html(context: ReviewContext) -> str:
        html = _inject_street_view_embed_key(
            inject_studio_navigation(render_registration_review_html(context.directory)),
            os.environ.get("GOOGLE_MAPS_EMBED_API_KEY"),
        )
        html = html.replace(
            "<head>",
            f'<head><base href="/city-assets/{context.city}/">',
            1,
        )
        html = html.replace(
            '"/api/reviews"',
            f'"/api/reviews?{urlencode({"city": context.city})}"',
        ).replace(
            '"/api/finalize"',
            f'"/api/finalize?{urlencode({"city": context.city})}"',
        )
        run_id = run_id_for_context(context)
        hidden = f'<input type="hidden" name="city" value="{context.city}">'
        if run_id:
            hidden += f'<input type="hidden" name="run" value="{run_id}">'
        html = html.replace(
            '<form action="/curate" method="get">',
            f'<form action="/curate" method="get">{hidden}',
            1,
        )
        return city_navigation(html, context)

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
            if path.startswith("/city-assets/"):
                asset_parts = path.removeprefix("/city-assets/").split("/", 1)
                if len(asset_parts) != 2:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                try:
                    context = context_for_city(unquote(asset_parts[0]))
                    asset = (context.directory / unquote(asset_parts[1])).resolve()
                    asset.relative_to(context.directory)
                    if not asset.is_file():
                        raise FileNotFoundError(asset)
                    self._content_response(
                        HTTPStatus.OK,
                        asset.read_bytes(),
                        mimetypes.guess_type(asset.name)[0] or "application/octet-stream",
                    )
                except (KeyError, OSError, ValueError):
                    self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                context = context_for_request(query, run_id)
            except (KeyError, ValueError) as error:
                self._json_response(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            if path in {"/", "/index.html"}:
                self._html_response(
                    city_navigation(
                        render_studio_home(len(run_catalog) > 0, len(run_catalog)),
                        context,
                    )
                )
                return
            if path in {"/registration", "/registration/"}:
                self._html_response(registration_html(context))
                return
            if path in {"/runs", "/runs/"}:
                self._html_response(city_navigation(RUN_HISTORY_HTML, context))
                return
            if path in {"/compare", "/compare/"}:
                self._html_response(city_navigation(CHIP_COMPARE_HTML, context))
                return
            if path in {"/model", "/model/"}:
                self._html_response(city_navigation(MODEL_DEBUG_HTML, context))
                return
            if path == "/curate":
                chip_id = query.get("chip", [None])[0]
                if not chip_id:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "chip is required"})
                    return
                try:
                    if not run_id:
                        run_id = run_id_for_context(context)
                    bundle = run_catalog.bundle(run_id)
                    write_context = context_for_run(bundle.evaluation_id)
                    if write_context is None:
                        raise ValueError(
                            "this evaluation has no registered city workspace"
                        )
                    threshold = float(
                        query.get("threshold", [bundle.metrics["confidence_threshold"]])[0]
                    )
                    if not 0 <= threshold <= 1:
                        raise ValueError("prediction confidence threshold must be between 0 and 1")
                    truth = bundle.ground_truth[bundle.ground_truth["chip_id"] == chip_id].copy()
                    result = append_validation_chip_to_registration_review(
                        write_context.config,
                        write_context.raster,
                        write_context.directory,
                        chip_id,
                        truth,
                    )
                except (KeyError, OSError, ValueError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                location = "/registration?" + urlencode(
                    {
                        "city": write_context.city,
                        "scene": str(result["scene_id"]),
                        "fullscreen": "1",
                        "run": bundle.evaluation_id,
                        "threshold": f"{threshold:.6g}",
                        "return": _safe_curation_return(query.get("return", [None])[0]),
                    }
                )
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", location)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path == "/api/reviews":
                try:
                    self._json_response(
                        HTTPStatus.OK,
                        load_persisted_reviews(context.directory),
                    )
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
                self._json_response(
                    HTTPStatus.OK,
                    bundle.summary()
                    | {"curation_available": context_for_run(bundle.evaluation_id) is not None},
                )
                return
            if path == "/api/runs":
                self._json_response(HTTPStatus.OK, run_catalog.summary())
                return
            if path == "/api/curation-status":
                try:
                    self._json_response(
                        HTTPStatus.OK,
                        _validation_chip_review_status(context.directory),
                    )
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if path.startswith("/api/runs/chip/"):
                chip_id = unquote(path.removeprefix("/api/runs/chip/"))
                try:
                    comparison = run_catalog.chip_comparison(chip_id, run_id)
                    comparison["curation_available"] = (
                        context_for_run(str(comparison["selected_run_id"])) is not None
                    )
                    self._json_response(
                        HTTPStatus.OK,
                        comparison,
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
            parsed = urlsplit(self.path)
            if parsed.path != "/api/reviews":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                context = context_for_request(parse_qs(parsed.query))
                result = persist_review_payload(context.directory, self._read_payload())
                result.update(
                    snapshot_registration_annotations(
                        context.config,
                        context.raster,
                        review_dir=context.directory,
                    )
                )
                self._json_response(HTTPStatus.OK, result)
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path != "/api/finalize":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                context = context_for_request(parse_qs(parsed.query))
                result = finalize_registration_feedback(
                    context.config,
                    context.raster,
                    review_dir=context.directory,
                )
                self._json_response(HTTPStatus.OK, result)
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    handler = partial(ReviewHandler, directory=str(default_context.directory))
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
