from __future__ import annotations

import json
import mimetypes
import os
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from urban_tree_ml.chip_catalog import next_training_truth, training_chip_catalog
from urban_tree_ml.backup_job import BackupJob
from urban_tree_ml.config import ProjectConfig, StudioConfig, load_config
from urban_tree_ml.curation_report import CURATION_REPORT_HTML, city_report
from urban_tree_ml.feedback import (
    ReviewStateConflictError,
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
from urban_tree_ml.studio_shell import render_studio_shell
from urban_tree_ml.training_queue import TRAINING_REVIEW_SCRIPT, load_queue, training_image
from urban_tree_ml.review_gallery import (
    GALLERY_HTML, read_collection, scoped_manifest, scene_state, gallery_page, apply_scene_patch,
)
from urban_tree_ml.quality import _render_registration_html
from urban_tree_ml.snapshot_worker import SnapshotWorker

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024
_CURATION_RETURN_PATHS = frozenset({"/registration", "/runs", "/compare", "/model", "/coverage", "/training-queue"})


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
            "more_done": bool(scene_reviews.get(scene_id, {}).get("more_done")),
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
        return render_studio_shell(
            html, context.city,
            {city: item.label for city, item in contexts.items()},
            {city: run_id_for_context(item) for city, item in contexts.items()},
        )


    def registration_html(context: ReviewContext, scene_id: str | None = None) -> str:
        if scene_id:
            with review_state_lock:
                manifest, _ = read_collection(context.directory)
                scoped = scoped_manifest(manifest, scene_id)
            page = _render_registration_html(scoped['samples'], scoped['metadata'], scoped['scenes'])
        else:
            page = GALLERY_HTML
        html = _inject_street_view_embed_key(
            inject_studio_navigation(page),
            os.environ.get("GOOGLE_MAPS_EMBED_API_KEY"),
        )
        html = html.replace(
            "<head>",
            f'<head><base href="/city-assets/{context.city}/">',
            1,
        )
        html = html.replace(
            '"/api/reviews"',
            (f'"/api/review-scene?{urlencode({"city": context.city, "scene": scene_id})}"' if scene_id else
             f'"/api/reviews?{urlencode({"city": context.city})}"'),
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
            from urban_tree_ml.crown_overlay import inject_crown_overlay
            html = inject_crown_overlay(html)
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
                context_query = query
                if path in {"/", "/runs", "/runs/", "/model", "/model/", "/coverage", "/coverage/", "/registration", "/registration/", "/api/review-gallery", "/api/model/all-summary"} and query.get("city") in (["all"], [""]):
                    if query.get('scene'):
                        raise ValueError('A curation scene requires its actual city, not all cities')
                    context_query = {key: value for key, value in query.items() if key != "city"}
                if path in {'/curate', '/curate-training', '/registration'} and query.get('city') and query['city'] != ['all']:
                    # Queue entries identify chips, not model snapshots. Resolve the
                    # currently selected model family to the destination cohort.
                    city = query['city'][0]
                    split = 'train' if path == '/curate-training' or (path == '/registration' and '::train-' in (run_id or '')) else 'validation'
                    for field in ('run', 'compare_run'):
                        value = query.get(field, [''])[0]
                        if not value or value == 'auto':
                            continue
                        family = value.split('::')[0]
                        match = next((r['run_id'] for r in run_catalog.summary()['runs']
                                      if r['training_run_id'] == family and r['city'].lower() == city
                                      and r['metrics'].get('split') == split), '')
                        if field == 'run' and not match and path == '/curate':
                            raise ValueError('Selected model has no validation inference for this city')
                        query[field] = [match]
                    run_id = query.get('run', [''])[0] or None
                context = context_for_request(context_query, run_id)
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
                try:
                    self._html_response(registration_html(context, query.get('scene', [None])[0]))
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path in {'/api/review-gallery', '/api/review-scene'}:
                try:
                    with review_state_lock:
                        if path == '/api/review-gallery' and query.get('city') == ['all']:
                            from urban_tree_ml.review_gallery import combined_gallery
                            result = combined_gallery({city: read_collection(owner.directory) for city, owner in contexts.items()}, query)
                        else:
                            manifest, state = read_collection(context.directory)
                            result = (gallery_page(manifest, state, query) if path.endswith('gallery') else
                                      scene_state(manifest, state, query.get('scene', [''])[0]))
                    self._json_response(HTTPStatus.OK, result)
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path in {"/coverage", "/coverage/"}:
                self._html_response(city_navigation(CURATION_REPORT_HTML, context))
                return
            if path == "/api/training-queue":
                try:
                    queue = load_queue(context.config.paths.root, context.city)
                    with review_state_lock:
                        manifest = json.loads((context.directory / "manifest.json").read_text())
                        state = load_persisted_reviews(context.directory)
                        progress = training_chip_catalog(context, manifest, state, queue['dataset'])
                    if progress is None:
                        raise ValueError('Training chip catalog unavailable')
                    pending = set(progress['pending'])
                    self._json_response(HTTPStatus.OK, {
                        'city': context.city, 'items': [
                            {**item, 'reviewed': item['chip_id'] not in pending,
                             'inference_available': bool(item.get('inference_run'))}
                            for item in queue['items']]})
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/training-image':
                try:
                    chip = query.get('chip', [''])[0]
                    from urban_tree_ml.training_queue import validate_training_image
                    validate_training_image(context, chip)
                    payload = training_image(context, chip)
                    self.send_response(HTTPStatus.OK)
                    self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == "/api/coverage":
                try:
                    with review_state_lock:
                        report = {"cities": [
                            city_report(city_context, run_catalog)
                            for city_context in contexts.values()
                        ]}
                    report['job'] = backup_job.status(context.config.paths.root)
                    self._json_response(HTTPStatus.OK, report)
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if path in {"/runs", "/runs/"}:
                self._html_response(city_navigation(RUN_HISTORY_HTML, context))
                return
            if path in {"/compare", "/compare/"}:
                self._html_response(city_navigation(CHIP_COMPARE_HTML, context))
                return
            if path in {"/model", "/model/"}:
                from urban_tree_ml.all_review import ALL_REVIEW_JS
                from urban_tree_ml.review_models import model_choices, model_selector
                choices = model_choices(run_catalog._discover_runs().values(), query.get('city', [context.city])[0])
                if not run_id and choices:
                    query['run'] = [choices[0]['run_id']]
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header('Location', '/model?' + urlencode(query, doseq=True))
                    self.end_headers()
                    return
                html = MODEL_DEBUG_HTML.replace('}init();', '}' + ALL_REVIEW_JS + TRAINING_REVIEW_SCRIPT)
                html = model_selector(html, choices, run_id)
                self._html_response(city_navigation(html, context))
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
                    with review_state_lock:
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
                        "radius": query.get('radius', [''])[0],
                        "return": _safe_curation_return(query.get("return", [None])[0]),
                        "queue": query.get("queue", [""])[0],
                        "queue_chip": chip_id,
                        "compare_run": query.get("compare_run", [""])[0],
                    }
                )
                self.send_response(HTTPStatus.SEE_OTHER)
                self.send_header("Location", location)
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return
            if path == "/curate-training":
                try:
                    with review_state_lock:
                        manifest = json.loads((context.directory / "manifest.json").read_text())
                        state = load_persisted_reviews(context.directory)
                        requested = query.get('chip', [None])[0]
                        queue = load_queue(context.config.paths.root, context.city)
                        progress = None
                        if requested:
                            # Assignments prioritize work; they are not an access
                            # list. Explicit chips must belong to this city's
                            # actual training split, including already reviewed chips.
                            progress = training_chip_catalog(context, manifest, state, None)
                            if not progress or requested not in progress['eligible']:
                                raise ValueError('Chip is not in this city\'s training split')
                            existing = next((s for s in manifest['scenes']
                                             if s.get('validation_chip_id') == requested
                                             and 'train' in s.get('splits', [])), None)
                            progress = {**(progress or {}), 'pending': [requested]}
                        else:
                            existing = None
                            progress = training_chip_catalog(context, manifest, state, None)
                        if existing:
                            result = existing
                        else:
                            chip, truth = next_training_truth(context, progress, manifest, state,
                                                              include_reviewed=bool(requested))
                            result = append_validation_chip_to_registration_review(
                                context.config, context.raster, context.directory, chip, truth,
                            )
                    location = "/registration?" + urlencode({
                        "city": context.city, "scene": result["scene_id"], "fullscreen": 1,
                        "queue": query.get('queue', [''])[0], "queue_chip": requested or '',
                        "run": run_id or '',
                        "compare_run": query.get("compare_run", [""])[0],
                        "return": _safe_curation_return(query.get('return', [
                            "/coverage?" + urlencode({"city": context.city})])[0]),
                        **{key: query[key][0] for key in ('threshold', 'radius') if key in query},
                    })
                    self.send_response(HTTPStatus.SEE_OTHER)
                    self.send_header("Location", location)
                    self.end_headers()
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            if path == "/api/reviews":
                try:
                    with review_state_lock:
                        reviews = load_persisted_reviews(context.directory)
                    self._json_response(
                        HTTPStatus.OK,
                        reviews,
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
            if path == '/api/model/all-summary':
                from urban_tree_ml.all_review import combined_summary
                try:
                    self._json_response(HTTPStatus.OK, combined_summary(run_catalog, run_id, context,
                                        contexts if query.get('city') == ['all'] else None))
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/backup-status':
                from urban_tree_ml.curation_archive import status
                from urban_tree_ml.feedback import _json_sha256

                try:
                    with review_state_lock:
                        manifest, state = read_collection(context.directory)
                        bundle_path = (context.config.paths.annotations / context.city
                                       / manifest['metadata']['review_id'] / 'bundle.json')
                        bundle = json.loads(bundle_path.read_text())
                        report = status(context.config.paths.root, context.city,
                                        state['state_revision'], bundle['feedback_current'] and bundle.get('review_state_sha256') == state['state_revision'],
                                        _json_sha256(manifest))
                        report['snapshot'] = snapshot_worker.status(context.city)
                    self._json_response(HTTPStatus.OK, report)
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/snapshot-status':
                self._json_response(HTTPStatus.OK, snapshot_worker.status(context.city))
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
                    comparison = run_catalog.chip_comparison(chip_id, run_id, query.get('compare_run', [None])[0])
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
            if parsed.path not in {"/api/reviews", "/api/review-scene"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            try:
                context = context_for_request(parse_qs(parsed.query))
                payload = self._read_payload()
                if "base_revision" not in payload:
                    raise ReviewStateConflictError(
                        "this review page predates safe saves; refresh it before editing"
                    )
                with review_state_lock:
                    result = (apply_scene_patch(context.directory, parse_qs(parsed.query).get('scene', [''])[0], payload)
                              if parsed.path == '/api/review-scene' else persist_review_payload(context.directory, payload))
                    snapshot_worker.request(context.city)
                    result['snapshot_pending'] = True
                self._json_response(HTTPStatus.OK, result)
            except ReviewStateConflictError as error:
                self._json_response(HTTPStatus.CONFLICT, {"error": str(error)})
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == '/api/backup':
                # Require our same-origin UI request, not a cross-site form POST.
                origin = self.headers.get('Origin')
                if (self.headers.get('X-Studio-Backup') != '1'
                        or (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
                    self._json_response(HTTPStatus.FORBIDDEN, {'error': 'Same-origin UI required'})
                    return
                try:
                    context = context_for_request(parse_qs(parsed.query))
                    started = backup_job.start(context.config.paths.root, context.config.paths.annotations)
                    self._json_response(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                                        {'started': started, 'error': None if started else 'Backup already running'})
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if parsed.path != "/api/finalize":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if parse_qs(parsed.query).get('city') == ['all']:
                from urban_tree_ml.city_scope import publish_cities
                # Serialize with normal saves/snapshots, publish saved state only.
                # Expose each city's outcome: partial success is never reported as all-success.
                with snapshot_worker.snapshot_lock, review_state_lock:
                    result = publish_cities(contexts, lambda owner: finalize_registration_feedback(
                        owner.config, owner.raster, review_dir=owner.directory))
                self._json_response(HTTPStatus.OK, result)
                return
            try:
                context = context_for_request(parse_qs(parsed.query))
                with snapshot_worker.snapshot_lock, review_state_lock:
                    result = finalize_registration_feedback(
                        context.config,
                        context.raster,
                        review_dir=context.directory,
                    )
                self._json_response(HTTPStatus.OK, result)
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})

    review_state_lock = Lock()
    snapshot_worker = SnapshotWorker(contexts, review_state_lock)
    backup_job = BackupJob(before_backup=snapshot_worker.flush, snapshot_lock=snapshot_worker.snapshot_lock)
    # Canonical reviews survive forced shutdown; rebuild any interrupted snapshots on startup.
    for city, context in contexts.items():
        if (context.directory / 'reviews.json').exists():
            snapshot_worker.request(city)
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
        snapshot_worker.close()
