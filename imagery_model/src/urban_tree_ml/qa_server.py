from __future__ import annotations

import json
import math
import polars as pl
import mimetypes
import os
from dataclasses import dataclass
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from time import perf_counter
from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit

from urban_tree_ml import street_examples
from urban_tree_ml.mosaic_coverage import missing_chips

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
from urban_tree_ml.save_timings import SaveTimings

_MAX_REVIEW_PAYLOAD_BYTES = 2 * 1024 * 1024
_CURATION_RETURN_PATHS = frozenset({"/registration", "/runs", "/compare", "/model", "/coverage", "/training-queue", "/species-debugger"})


def _json_safe(value: object) -> object:
    """Represent unavailable numeric metrics as null without changing cached data."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _context_matches_bundle(context, bundle):
    """Reuse the original annotation workspace only for identical image footprints."""
    if context.config.inventory.city.lower() != bundle.config.inventory.city.lower():
        return False
    if getattr(bundle.config.imagery, 'acquisition_id', None):
        from urban_tree_ml.temporal_studio import matches_prediction_raster
        return matches_prediction_raster(context, bundle)
    from urban_tree_ml.resolution_recipe import same_chip_footprints, validate_resolution_derivative, same_vrt_sources
    if not same_chip_footprints(context.raster, context.config.imagery.chip_pixels,
                                bundle.raster_path, bundle.config.imagery.chip_pixels):
        return False
    if context.raster != bundle.raster_path:
        if same_vrt_sources(context.raster, bundle.raster_path):
            return True
        try:
            validate_resolution_derivative(context.raster, bundle.raster_path)
        except (OSError, ValueError):
            return False
    return True


@dataclass(frozen=True)
class ReviewContext:
    city: str
    label: str
    config: ProjectConfig
    raster: Path
    directory: Path
    evaluation_dir: Path
    acquisition_id: str | None = None


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
    from urban_tree_ml.catalog_imagery import matches_catalog_imagery
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not matches_catalog_imagery(config.paths.root, manifest.get('metadata', {}).get('source_raster'), raster):
        raise ValueError('Registration workspace does not belong to the configured imagery')
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
    from urban_tree_ml.temporal_studio import load_contexts
    acquisitions, acquisition_records = load_contexts(contexts, _checked_review_context, load_config)
    _serve_review_contexts(
        contexts,
        default_city=studio.default_city,
        bind=bind,
        port=port,
        acquisitions=acquisitions,
        acquisition_records=acquisition_records,
    )


def _serve_review_contexts(
    contexts: dict[str, ReviewContext],
    *,
    default_city: str,
    bind: str,
    port: int,
    acquisitions=None,
    acquisition_records=None,
) -> None:
    acquisitions = acquisitions or {}
    acquisition_records = acquisition_records or {}
    all_contexts = {**contexts, **acquisitions}
    def workspace_key(context):
        return context.acquisition_id if context.acquisition_id in acquisitions else context.city
    from urban_tree_ml import chip_tags, chip_insights
    from urban_tree_ml.chip_insights_ui import INSIGHTS_HTML, inject_editor
    insight_service = chip_insights.InsightService()
    from urban_tree_ml import species_debugger
    from urban_tree_ml.species_debugger_ui import SPECIES_DEBUGGER_HTML
    species_service = chip_insights.InsightService()
    save_timings = SaveTimings()
    from urban_tree_ml.backup_status import ManifestDigests
    manifest_digests = ManifestDigests()
    from urban_tree_ml.review_native import backend_info
    print(f"Review acceleration: {backend_info()['backend']} (JSON encoding, container copies and ranking)")
    default_context = contexts[default_city]
    run_catalog = RunDebugCatalog(
        default_context.config,
        default_context.evaluation_dir,
        default_context.raster,
    )

    def context_for_city(city: str | None) -> ReviewContext:
        selected = city or default_city
        if selected in acquisitions:
            return acquisitions[selected]
        acquisition_owner = next((c for c in contexts.values() if c.acquisition_id == selected), None)
        if acquisition_owner is not None:
            return acquisition_owner
        if selected not in contexts:
            raise KeyError(f"unknown studio city {selected!r}")
        return contexts[selected]

    def context_for_run(run_id: str | None) -> ReviewContext | None:
        if not run_id:
            return None
        from urban_tree_ml.queue_navigation import model_identity
        record = next((r for r in run_catalog.records() if r['run_id'] == run_id), None)
        if record is None:
            return None
        bundle = model_identity(record)
        return next(
            (
                context
                for context in all_contexts.values()
                if _context_matches_bundle(context, bundle)
            ),
            None,
        )

    def context_for_request(
        query: dict[str, list[str]],
        run_id: str | None = None,
    ) -> ReviewContext:
        requested_city = query.get("city", [None])[0]
        acquisition = query.get('acquisition', [None])[0]
        if acquisition:
            owner = next((c for c in all_contexts.values() if c.acquisition_id == acquisition), None)
            if owner is None or (requested_city and requested_city != owner.city):
                raise ValueError('Unknown acquisition or mismatched city')
            if run_id and context_for_run(run_id) != owner:
                raise ValueError('Selected model does not use this imagery acquisition')
            return owner
        city_context = context_for_city(requested_city) if requested_city else None
        run_context = context_for_run(run_id)
        if city_context is not None and run_context is not None and city_context.city != run_context.city:
            raise ValueError("requested city does not own the selected validation run")
        return run_context or city_context or default_context

    def run_id_for_context(context: ReviewContext, available=None) -> str | None:
        training_run_id = context.evaluation_dir.parent.parent.name
        cohort = context.evaluation_dir.name
        candidate = (
            training_run_id
            if cohort == "validation"
            else f"{training_run_id}::{cohort}"
        )
        if available is None:
            available = {str(record["run_id"]) for record in run_catalog.records()}
        return candidate if candidate in available else None

    def city_navigation(html: str, context: ReviewContext) -> str:
        available = {str(record['run_id']) for record in run_catalog.records()}
        return render_studio_shell(
            html, context.city,
            {city: item.label for city, item in contexts.items()},
            {city: run_id_for_context(item, available) for city, item in contexts.items()},
        )


    def registration_html(context: ReviewContext, scene_id: str | None = None, selected_run=None) -> str:
        if scene_id:
            with review_state_lock:
                manifest, _ = read_collection(context.directory)
                scoped = scoped_manifest(manifest, scene_id)
            scoped['metadata'] = {**scoped['metadata'], 'peer_images_enabled':
                sum(r['city'] == context.city for r in acquisition_records.values()) > 1}
            from urban_tree_ml.review_context import CONTEXT_PIXELS, scene_image_urls
            scene = scoped['scenes'][0]
            image, surrounding = scene_image_urls(context, scene, scene['image'])
            scoped['scenes'] = [{**scene, 'image': image, 'context_pixels': CONTEXT_PIXELS,
                                 'context_image': surrounding}]
            page = _render_registration_html(scoped['samples'], scoped['metadata'], scoped['scenes'])
            from html import escape
            from urban_tree_ml.temporal_studio import tile_label
            label = tile_label(context.city, scoped['scenes'][0].get('validation_chip_id') or scene_id,
                               context.acquisition_id)
            page = page.replace('<h1>Grouped registration review</h1>', '<h1>'+escape(label)+'</h1>')
        else:
            page = GALLERY_HTML
        html = _inject_street_view_embed_key(
            inject_studio_navigation(page),
            street_examples.credential("GOOGLE_MAPS_EMBED_API_KEY"),
        )
        if scene_id and street_examples.credential("MAPILLARY_KEY"):
            script = Path(__file__).with_name("street_examples.js").read_text(encoding="utf-8")
            html = html.replace("const mapillaryAvailable = false;", "const mapillaryAvailable = true;\n" + script)
        html = html.replace(
            "<head>",
            f'<head><base href="/city-assets/{workspace_key(context)}/">',
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
        if scene_id and (selected_run or run_id):
            chip = scoped['scenes'][0].get('validation_chip_id')
            if chip:
                html = inject_editor(html, (selected_run or run_id, chip, context.city))
        from urban_tree_ml.temporal_studio import inject_acquisition_controls
        return inject_acquisition_controls(city_navigation(html, context), context, acquisition_records)

    class ReviewHandler(SimpleHTTPRequestHandler):
        def _query(self, parsed):
            query = parse_qs(parsed.query)
            acquisition = self.headers.get('X-Curation-Acquisition')
            if acquisition:
                if query.get('acquisition', [acquisition]) != [acquisition]:
                    raise ValueError('Conflicting acquisition identities')
                query['acquisition'] = [acquisition]
            return query
        def _json_response(self, status: HTTPStatus, payload: object) -> None:
            encoded = (json.dumps(_json_safe(payload), sort_keys=True, allow_nan=False) + "\n").encode()
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
            query = self._query(parsed)
            run_id = query.get("run", [None])[0]
            if path in {"/api/street/nearby", "/api/street/photo"}:
                try:
                    if path.endswith("/nearby"):
                        self._json_response(HTTPStatus.OK, street_examples.nearby(query["lat"][0], query["lon"][0]))
                    else:
                        meta, data = street_examples.photo(query["id"][0])
                        creator = (meta.get("creator") or {}).get("username", "")
                        self.send_response(HTTPStatus.OK)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(data)))
                        self.send_header("X-Creator", quote(creator))
                        self.end_headers()
                        self.wfile.write(data)
                except (KeyError, TypeError, ValueError):
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "Invalid street imagery request"})
                except RuntimeError as exc:
                    self._json_response(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                except OSError:
                    self._json_response(HTTPStatus.BAD_GATEWAY, {"error": "Street imagery unavailable"})
                return
            if path in {'/model', '/model/', '/api/model/all-summary'}:
                from urban_tree_ml.review_models import selected_model_review_id, default_review_model
                if not run_id:
                    city = query.get('city', [default_context.city])[0] or default_context.city
                    preferred = getattr(contexts.get(city), 'acquisition_id', None)
                    selected = default_review_model(run_catalog.records(), city,
                        query.get('acquisition', [None])[0], preferred)
                    if selected is None:
                        self._json_response(HTTPStatus.NOT_FOUND, {'error': 'No review model is available for the selected city and imagery'})
                        return
                    query['run'] = [selected['run_id']]
                    from urban_tree_ml.temporal_studio import record_acquisition
                    acquisition = record_acquisition(selected)
                    if acquisition and not query.get('acquisition') and city != 'all':
                        query['acquisition'] = [acquisition]
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header('Location', path + '?' + urlencode(query, doseq=True))
                    self.end_headers()
                    return
                repaired = selected_model_review_id(run_id)
                if repaired != run_id:
                    query['run'] = [repaired]
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header('Location', path + '?' + urlencode(query, doseq=True))
                    self.end_headers()
                    return
            if path == '/recombinant' or path.startswith('/api/recombinant'):
                from urban_tree_ml import recombinant
                from urban_tree_ml.recombinant_ui import HTML
                root = default_context.config.paths.root
                try:
                    root = recombinant.scoped(root, query.get('source', [None])[0])
                    if path == '/recombinant':
                        self._html_response(city_navigation(HTML, contexts.get('usbos', default_context)))
                    elif path == '/api/recombinant':
                        self._json_response(HTTPStatus.OK, recombinant.catalog(root))
                    elif path == '/api/recombinant/image':
                        self._content_response(HTTPStatus.OK, recombinant.png(recombinant.pixels(root, query.get('chip', [''])[0])), 'image/png')
                    elif path == '/api/recombinant/overview':
                        self._content_response(HTTPStatus.OK, (recombinant.workspace(root)/'overview.png').read_bytes(), 'image/png')
                    elif path == '/api/recombinant/component':
                        self._content_response(HTTPStatus.OK, recombinant.component_image(root, query.get('id', [''])[0]), 'image/png')
                    elif path == '/api/recombinant/generated':
                        self._content_response(HTTPStatus.OK, recombinant.generated_image(root, query.get('id', [''])[0], query.get('variant', ['blended'])[0]), 'image/png')
                    elif path in {'/api/recombinant/source-point','/api/recombinant/curate'}:
                        from urban_tree_ml.recombinant_navigation import locate, with_latest_predictions
                        owner=contexts.get(recombinant.source(root)['city'])
                        if owner is None: raise ValueError('No matching city curation workspace is loaded')
                        with review_state_lock:
                            target=locate(root,query.get('id',[''])[0],query.get('x',[''])[0],query.get('y',[''])[0],owner)
                        # Loading model bundles must not hold the annotation/save lock.
                        target=with_latest_predictions(root,target,owner,run_catalog,_context_matches_bundle)
                        with review_state_lock:
                            if path.endswith('/curate') and target['scene_id'] is None:
                                manifest=json.loads((owner.directory/'manifest.json').read_text())
                                result=next((s for s in manifest['scenes'] if s.get('validation_chip_id')==target['chip_id']),None)
                                if result is None:
                                    result=append_validation_chip_to_registration_review(owner.config,owner.raster,
                                        owner.directory,target['chip_id'],pl.DataFrame())
                                target['scene_id']=result['scene_id']
                        if path.endswith('/curate'):
                            self.send_response(HTTPStatus.SEE_OTHER)
                            self.send_header('Location','/registration?'+urlencode({'city':owner.city,
                                'scene':target['scene_id'],'fullscreen':1,'run':target['run_id'] or '',
                                'threshold':'.13',
                                'source_predictions':'available' if target['run_id'] else 'unavailable',
                                'return':'/recombinant?source='+recombinant.source(root)['source_id']}))
                            self.send_header('Cache-Control','no-store'); self.end_headers()
                        else:
                            self._json_response(HTTPStatus.OK,target)
                    elif path == '/api/recombinant/annotations':
                        from urban_tree_ml.recombinant_labels import preview_saved
                        owner=contexts.get(recombinant.source(root)['city'])
                        if owner is None:
                            raise ValueError('No matching city curation workspace is loaded')
                        self._json_response(HTTPStatus.OK, preview_saved(root,query.get('id',[''])[0],owner))
                    else:
                        self._json_response(HTTPStatus.NOT_FOUND, {'error': 'Unknown recombinant endpoint'})
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/save-timings':
                self._json_response(HTTPStatus.OK, {'recent': save_timings.recent(),
                    'acceleration': backend_info(),
                    'note': 'Server processing only; excludes browser queue and network transit. No annotation contents recorded.'})
                return
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
                all_imagery_view = (path in {'/model','/model/','/api/model/all-summary'}
                                    and query.get('acquisition') == ['all'])
                if all_imagery_view:
                    context_query = {key:value for key,value in query.items() if key != 'acquisition'}
                if path in {"/", "/runs", "/runs/", "/model", "/model/", "/coverage", "/coverage/", "/registration", "/registration/", "/api/review-gallery", "/api/model/all-summary", "/insights", "/api/chip-insights", "/model-profile", "/api/model-profile", "/species-debugger", "/api/species-debugger", "/api/species-debugger/options"} and query.get("city") in (["all"], [""]):
                    if query.get('scene'):
                        raise ValueError('A curation scene requires its actual city, not all cities')
                    context_query = {key: value for key, value in context_query.items() if key != "city"}
                if path in {'/curate', '/curate-training', '/registration'} and query.get('city') and query['city'] != ['all']:
                    # Queue entries identify chips, not model snapshots. Resolve the
                    # currently selected model family to the destination cohort.
                    city = query['city'][0]
                    split = 'train' if path == '/curate-training' or (path == '/registration' and '::train-' in (run_id or '')) else 'validation'
                    records = run_catalog.records()
                    for field in ('run', 'compare_run'):
                        value = query.get(field, [''])[0]
                        if not value or value == 'auto':
                            continue
                        family = value.split('::')[0]
                        from urban_tree_ml.temporal_studio import resolve_cohort
                        match = resolve_cohort(records, value, city, split, query.get('acquisition', [None])[0])
                        if field == 'run' and not match and path == '/curate':
                            raise ValueError('Selected model has no validation inference for this city')
                        query[field] = [match]
                    run_id = query.get('run', [''])[0] or None
                context = context_for_request(context_query, None if all_imagery_view or path in {'/insights', '/api/chip-insights', '/api/review-next', '/api/review-next-image', '/model-profile', '/api/model-profile'} else run_id)
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
                    self._html_response(registration_html(context, query.get('scene', [None])[0], run_id))
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/peer-image':
                try:
                    from urban_tree_ml.temporal_studio import peer_preview
                    with review_state_lock:
                        result = peer_preview(context, query.get('scene', [''])[0], all_contexts, acquisition_records,
                                              revert=query.get('action') == ['revert'])
                    self._json_response(HTTPStatus.OK, result)
                except (ValueError, KeyError, OSError, StopIteration) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/review-next-image':
                try:
                    from urban_tree_ml.queue_navigation import queue_image, model_identity
                    record = next((r for r in run_catalog.records() if r['run_id'] == run_id), None)
                    if record is None or not _context_matches_bundle(context, model_identity(record)):
                        raise ValueError('Selected model does not share this curation imagery')
                    split = query.get('split', [''])[0]
                    if split != record['metrics'].get('split', 'validation'):
                        raise ValueError('Queue and model splits differ')
                    image = queue_image(context, record, query.get('chip', [''])[0], split,
                                        int(query.get('border', ['0'])[0]))
                    self._content_response(HTTPStatus.OK, image, 'image/png')
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/review-next':
                try:
                    from urban_tree_ml.queue_navigation import prepare_member
                    with review_state_lock:
                        manifest, _ = read_collection(context.directory)
                    result = prepare_member(context, run_catalog.records(), manifest,
                        query.get('chip', [''])[0], query.get('split', [''])[0],
                        run_id, query.get('compare_run', ['auto'])[0])
                    self._json_response(HTTPStatus.OK, result)
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/review-context':
                try:
                    from urban_tree_ml.review_context import CONTEXT_PIXELS, scene_context_image
                    with review_state_lock:
                        manifest, _ = read_collection(context.directory)
                        scoped = scoped_manifest(manifest, query.get('scene', [''])[0])
                    border = int(query.get('border', [CONTEXT_PIXELS])[0])
                    self._content_response(HTTPStatus.OK, scene_context_image(context, scoped, border), 'image/png')
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
                        if path == '/api/review-scene':
                            from urban_tree_ml.border_inventory import neighboring_inventory
                            result['border_inventory'] = neighboring_inventory(context, manifest, state, query.get('scene', [''])[0])
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
            if path == '/model-profile':
                from urban_tree_ml.model_profile_ui import PROFILE_HTML
                from urban_tree_ml.review_models import model_choices, model_selector
                choices = model_choices(run_catalog.records())
                self._html_response(city_navigation(model_selector(PROFILE_HTML, choices, run_id), context))
                return
            if path == '/api/model-profile':
                from urban_tree_ml.model_profile import load_profile
                try:
                    family = (run_id or '').split('::')[0]
                    record = next((r for r in run_catalog.records() if r['training_run_id'] == family), None)
                    if record is None:
                        raise ValueError('Choose an available model')
                    self._json_response(HTTPStatus.OK, load_profile(run_catalog.config.paths.root, family))
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/insights':
                from urban_tree_ml.review_models import model_choices, model_selector
                choices = model_choices(run_catalog.records())
                html = model_selector(INSIGHTS_HTML, choices, run_id)
                self._html_response(city_navigation(inject_editor(html), context))
                return
            if path == '/species-debugger':
                self._html_response(city_navigation(SPECIES_DEBUGGER_HTML.replace('__CITY__',context.city), context))
                return
            if path.startswith('/api/species-debugger'):
                try:
                    root = context.config.paths.root
                    scope = query.get('city', [context.city])[0] or context.city
                    split = query.get('split', ['validation'])[0]
                    if path == '/api/species-debugger/options':
                        self._json_response(HTTPStatus.OK, {'models': species_debugger.scope_options(run_catalog,contexts,scope,split)})
                    elif path == '/api/species-debugger/label':
                        with review_state_lock:
                            manifest,state = read_collection(context.directory)
                            target = species_debugger.label_target(manifest,state,query.get('chip',[''])[0],query.get('tree',[''])[0])
                        self._json_response(HTTPStatus.OK,target)
                    elif path == '/api/species-debugger/crop':
                        bundle=run_catalog.bundle(query.get('image_run',[''])[0])
                        if bundle.metrics.get('split') not in ('train','validation') or not _context_matches_bundle(context,bundle):
                            raise ValueError('Crop must belong to this city and development imagery')
                        self._content_response(HTTPStatus.OK,species_debugger.crop_png(bundle,query.get('chip',[''])[0],
                            query.get('x',[''])[0],query.get('y',[''])[0],query.get('span',['38.4'])[0]),'image/png')
                    elif path == '/api/species-debugger':
                        a=query.get('a',[''])[0]; b=query.get('b',[''])[0]
                        threshold=float(query.get('threshold',['.13'])[0]); policy=query.get('policy',['crown-half-radius-2-4m'])[0]
                        basis=query.get('basis',['matched'])[0]
                        valid={o['id'] for o in species_debugger.scope_options(run_catalog,contexts,scope,split)}
                        if a not in valid or b not in valid:raise ValueError('Choose two available models')
                        versions=tuple((r['run_id'],run_catalog._file_versions([
                            r['evaluation_dir']/'predictions.parquet',r['evaluation_dir']/'ground-truth.parquet']))
                            for r in run_catalog.records() if (scope=='all' or r['city'].lower()==scope) and r['metrics'].get('split')==split)
                        key=(scope,split,a,b,threshold,policy,basis,versions)
                        def calculate(progress):
                            return species_debugger.compare_scope(run_catalog,contexts,scope,split,a,b,
                                threshold,policy,basis,_context_matches_bundle,progress)
                        result=species_service.request(key,calculate)
                        if result['state']=='ready':
                            result={'state':'ready','split':split,**species_debugger.page(result['rows'],query.get('species',[''])[0],
                                query.get('status',['all'])[0],query.get('scope',['any'])[0],int(query.get('page',['0'])[0]),
                                auto_pick=query.get('auto',['0'])[0]=='1')}
                        self._json_response(HTTPStatus.OK,result)
                    else:
                        self.send_error(HTTPStatus.NOT_FOUND)
                except (ValueError,KeyError,OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST,{'error':str(error)})
                return
            if path in {"/compare", "/compare/"}:
                self._html_response(city_navigation(CHIP_COMPARE_HTML, context))
                return
            if path in {"/model", "/model/"}:
                from urban_tree_ml.all_review import ALL_REVIEW_JS
                from urban_tree_ml.review_models import model_choices, model_selector
                from urban_tree_ml.temporal_studio import acquisition_records as filter_acquisitions, inject_model_acquisitions
                records = run_catalog.records()
                selected_acquisition = query.get('acquisition', [None])[0]
                from urban_tree_ml.temporal_studio import record_acquisition
                exact = next((r for r in records if r['run_id'] == run_id), None)
                inferred = record_acquisition(exact) if exact else None
                if inferred and not selected_acquisition:
                    query['acquisition'] = [inferred]
                    query['city'] = [exact['city'].lower()]
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header('Location', '/model?' + urlencode(query, doseq=True))
                    self.end_headers()
                    return
                choices = model_choices(filter_acquisitions(records, selected_acquisition), query.get('city', [context.city])[0])
                if not run_id and choices:
                    query['run'] = [choices[0]['run_id']]
                    self.send_response(HTTPStatus.FOUND)
                    self.send_header('Location', '/model?' + urlencode(query, doseq=True))
                    self.end_headers()
                    return
                html = MODEL_DEBUG_HTML.replace('}init();', '}' + ALL_REVIEW_JS + TRAINING_REVIEW_SCRIPT)
                html = model_selector(html, choices, run_id)
                html = inject_model_acquisitions(html, context, acquisition_records, records, run_id, selected_acquisition)
                self._html_response(city_navigation(inject_editor(html), context))
                return
            if path in {'/curate', '/curate-training'} and run_id and query.get('chip'):
                # Revisiting a materialized chip is navigation, not a gallery rebuild.
                # Validate its dataset membership without computing city-wide coverage.
                from urban_tree_ml.queue_navigation import prepare_member
                requested = query['chip'][0]
                split = 'train' if path == '/curate-training' else 'validation'
                with review_state_lock:
                    manifest, _ = read_collection(context.directory)
                existing = next((s for s in manifest['scenes'] if s.get('validation_chip_id') == requested
                                 and split in s.get('splits', [])), None)
                if existing:
                    try:
                        prepared = prepare_member(context, run_catalog.records(), manifest,
                            requested, split, run_id, query.get('compare_run', ['auto'])[0])
                    except FileNotFoundError:
                        pass  # Legacy datasets without manifests retain the original path.
                    except (ValueError, KeyError, OSError) as error:
                        self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                        return
                    else:
                        destination = {k: query[k][0] for k in ('queue', 'threshold', 'radius', 'acquisition') if k in query}
                        if prepared['inference_unavailable']:
                            destination['source_predictions'] = 'unavailable'
                        destination.update(city=context.city, scene=prepared['scene'], fullscreen='1',
                            queue_chip=requested, run=prepared['run'], compare_run=prepared['compare_run'],
                            **{'return': _safe_curation_return(query.get('return', [None])[0])})
                        self.send_response(HTTPStatus.SEE_OTHER)
                        self.send_header('Location', '/registration?' + urlencode(destination))
                        self.send_header('Cache-Control', 'no-store')
                        self.end_headers()
                        return
            if path == "/curate":
                chip_id = query.get("chip", [None])[0]
                if not chip_id:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": "chip is required"})
                    return
                try:
                    if not run_id:
                        run_id = run_id_for_context(context)
                    bundle = run_catalog.review_chip_bundle(run_id, chip_id)
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
                    if bundle.metrics.get('split', 'validation') != 'validation':
                        raise ValueError('Choose a validation run for validation curation')
                    if missing_chips(bundle.raster_path, bundle.config.imagery.chip_pixels, [chip_id]):
                        raise ValueError('This tile has insufficient source imagery coverage')
                    has_inference = chip_id in bundle.chip_ids
                    if not has_inference:
                        from urban_tree_ml.queue_navigation import require_chip
                        require_chip(write_context, run_catalog.record(run_id), chip_id, 'validation')
                    truth = bundle.truth_for_chip(chip_id)
                    with review_state_lock:
                        if not has_inference:
                            manifest, state = read_collection(write_context.directory)
                            if manifest['metadata'].get('curation_schema_version') != 2:
                                from urban_tree_ml.chip_catalog import inventory_truth_for_chip
                                truth = inventory_truth_for_chip(write_context, bundle.config.dataset, chip_id,
                                    'validation', manifest, state, include_reviewed=True,
                                    id_chip_pixels=bundle.config.imagery.chip_pixels)
                        result = append_validation_chip_to_registration_review(
                            write_context.config,
                            write_context.raster,
                            write_context.directory,
                            chip_id,
                            truth,
                            id_chip_pixels=bundle.config.imagery.chip_pixels,
                            chip_split='validation',
                            _render_gallery=False,
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
                        **({} if has_inference else {"source_predictions": "unavailable"}),
                        "threshold": f"{threshold:.6g}",
                        "radius": query.get('radius', [''])[0],
                        "return": _safe_curation_return(query.get("return", [None])[0]),
                        "queue": query.get("queue", [""])[0],
                        "queue_chip": chip_id,
                        **({'acquisition': query['acquisition'][0]} if query.get('acquisition') else {}),
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
                        manifest, state = read_collection(context.directory)
                        requested = query.get('chip', [None])[0]
                        progress = None
                        from urban_tree_ml.queue_navigation import model_identity, require_chip
                        record = next((r for r in run_catalog.records() if r['run_id'] == run_id), None)
                        selected_bundle = model_identity(record) if record else None
                        if selected_bundle and not _context_matches_bundle(context, selected_bundle):
                            raise ValueError('Selected model does not share this curation imagery')
                        selected_dataset = selected_bundle.config.dataset if selected_bundle else None
                        id_chip_pixels = selected_bundle.config.imagery.chip_pixels if selected_bundle else None
                        if requested:
                            # Assignments prioritize work; they are not an access
                            # list. Explicit chips must belong to this city's
                            # actual training split, including already reviewed chips.
                            if record:
                                require_chip(context, record, requested, 'train')
                                progress = {'dataset': selected_dataset}
                            else:
                                progress = training_chip_catalog(context, manifest, state, selected_dataset)
                                if not progress or requested not in progress['eligible']:
                                    raise ValueError('Chip is not in this city\'s training split')
                            existing = next((s for s in manifest['scenes']
                                             if s.get('validation_chip_id') == requested
                                             and 'train' in s.get('splits', [])), None)
                            progress = {**(progress or {}), 'pending': [requested]}
                        else:
                            existing = None
                            progress = training_chip_catalog(context, manifest, state, selected_dataset)
                        if existing:
                            result = existing
                        else:
                            if requested and manifest['metadata'].get('curation_schema_version') == 2:
                                # The image-scoped editor gathers original inventory
                                # itself; frozen labels may include manual-only IDs.
                                chip, truth = requested, pl.DataFrame()
                            else:
                                chip, truth = next_training_truth(context, progress, manifest, state,
                                                                  include_reviewed=bool(requested), id_chip_pixels=id_chip_pixels)
                            result = append_validation_chip_to_registration_review(
                                context.config, context.raster, context.directory, chip, truth,
                                id_chip_pixels=id_chip_pixels,
                                _render_gallery=False,
                            )
                    location = "/registration?" + urlencode({
                        "city": context.city, "scene": result["scene_id"], "fullscreen": 1,
                        "queue": query.get('queue', [''])[0], "queue_chip": requested or '',
                        "run": run_id or '',
                        "compare_run": query.get("compare_run", [""])[0],
                        "return": _safe_curation_return(query.get('return', [
                            "/coverage?" + urlencode({"city": context.city})])[0]),
                        **{key: query[key][0] for key in ('threshold', 'radius', 'acquisition') if key in query},
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
                owner = context_for_run(bundle.evaluation_id)
                unscored = False
                if owner and bundle.metrics.get('split', 'validation') == 'validation':
                    from urban_tree_ml.queue_navigation import review_split_ids
                    try:
                        grid = review_split_ids(owner, run_catalog.record(bundle.evaluation_id), 'validation')
                        unscored = not grid.issubset(bundle.chip_ids)
                    except FileNotFoundError:
                        pass
                self._json_response(HTTPStatus.OK, bundle.summary() | {
                    "no_imagery": sorted(missing_chips(bundle.raster_path, bundle.config.imagery.chip_pixels,
                                                       bundle.chip_ids)),
                    "curation_available": owner is not None, "has_additional_validation_chips": unscored})
                return
            if path == '/api/model/ranking':
                try:
                    threshold = query.get('threshold', ['0.35'])[0]
                    radius = query.get('radius', ['2'])[0]
                    if backend_info()['backend'] == 'python':
                        from urban_tree_ml.review_ranking import ranking
                        rows = list(ranking(run_catalog.bundle(run_id), threshold, radius))
                        extra = run_catalog.review_backfill(run_catalog.record(run_id))
                        if extra is not None:
                            rows.extend(ranking(extra, threshold, radius))
                    else:
                        from urban_tree_ml.dynamic_ranking import rank_evaluation
                        rows = rank_evaluation(run_catalog.record(run_id), threshold, radius)
                    self._json_response(HTTPStatus.OK, {'chips': rows})
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/model/all-summary':
                from urban_tree_ml.all_review import combined_summary
                try:
                    self._json_response(HTTPStatus.OK, combined_summary(run_catalog, run_id, context,
                                        all_contexts if query.get('acquisition') == ['all'] else contexts if query.get('city') == ['all'] else None,
                                        validation_only=query.get('split') == ['validation'],
                                        all_imagery=query.get('acquisition') == ['all'],
                                        scope_city=query.get('city', [context.city])[0]))
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/backup-status':
                from urban_tree_ml.curation_archive import status

                try:
                    with review_state_lock:
                        manifest, state = read_collection(context.directory)
                    # Cached collections are immutable. Status-file IO and a
                    # cold manifest hash must not hold the interactive save lock.
                    bundle_path = (context.config.paths.annotations / context.city
                                   / manifest['metadata']['review_id'] / 'bundle.json')
                    bundle = json.loads(bundle_path.read_text()) if bundle_path.exists() else {}
                    report = status(context.config.paths.root, context.city,
                                    state['state_revision'], bundle.get('feedback_current', False) and bundle.get('review_state_sha256') == state['state_revision'],
                                    manifest_digests.get(context.directory, manifest), chip_tags.tag_digest(context),
                                    review_id=manifest['metadata']['review_id'])
                    report['snapshot'] = snapshot_worker.status(workspace_key(context))
                    report['job'] = backup_job.status(context.config.paths.root)
                    self._json_response(HTTPStatus.OK, report)
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/snapshot-status':
                self._json_response(HTTPStatus.OK, snapshot_worker.status(workspace_key(context)))
                return
            if path == "/api/runs":
                self._json_response(HTTPStatus.OK, run_catalog.summary())
                return
            if path == '/api/chip-tags':
                try:
                    bundle = run_catalog.review_chip_bundle(run_id, query.get('chip', [''])[0])
                    if not _context_matches_bundle(context, bundle):
                        raise ValueError('Model does not share this curation imagery')
                    with review_state_lock:
                        result = chip_tags.chip_tags(context, bundle, query.get('chip', [''])[0])
                    self._json_response(HTTPStatus.OK, result)
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if path == '/api/chip-insights':
                try:
                    threshold = chip_insights.validate_options(query.get('threshold', ['0.13'])[0],
                                query.get('policy', [chip_insights.POLICIES[0]])[0], query.get('split', ['validation'])[0])
                    policy, split = query.get('policy', [chip_insights.POLICIES[0]])[0], query.get('split', ['validation'])[0]
                    family = (run_id or '').split('::')[0]
                    records = [r for r in run_catalog.records() if r['training_run_id'] == family
                               and r['city'].lower() in contexts and r['metrics'].get('split') in ('train', 'validation')
                               and (bool(r['metrics'].get('prediction_only')) if split == 'prediction-only' else
                                    r['metrics'].get('split') == split and not r['metrics'].get('prediction_only'))]
                    if not records:
                        raise ValueError('No development inference for this model/split')
                    paths = [r['evaluation_dir'] / name for r in records for name in
                             ('metrics.json','evaluation-metadata.json','predictions.parquet','ground-truth.parquet','taxonomy.json','inference-coverage.json')]
                    key = (family, split, threshold, policy, run_catalog._file_versions(paths))
                    result = insight_service.request(key, lambda progress: chip_insights.collect(
                        run_catalog, contexts, records, threshold, policy, _context_matches_bundle, progress))
                    if result['state'] == 'ready':
                        with review_state_lock:
                            collections = {city: read_collection(contexts[city].directory)
                                           for city in {r['city'] for r in result['rows']}}
                        # Cached annotation snapshots are immutable. Aggregate
                        # outside the save lock so insights cannot delay edits.
                        rows = chip_insights.decorate(result['rows'], contexts, collections=collections)
                        result = dict(state='ready', tags=['(untagged)', *sorted({t for r in rows for t in r['tags']})],
                                      species_options=sorted({s for r in rows for s in [*r['species'], *r['conflicts']]}),
                                      **chip_insights.aggregate(rows, city=query.get('city', ['all'])[0],
                                          tag=query.get('tag', [''])[0], species=query.get('species', [''])[0],
                                          flags_only=query.get('flags', ['0'])[0] == '1',
                                          include_shifted=query.get('shifts', ['1'])[0] == '1'))
                    self._json_response(HTTPStatus.OK, result)
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
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
                    comparison = run_catalog.chip_comparison(chip_id, run_id, query.get('compare_run', [None])[0],
                                                           query.get('preferred_run', [None])[0])
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
                    self._json_response(HTTPStatus.OK, run_catalog.review_chip_bundle(run_id, chip_id).chip(chip_id))
                except KeyError as error:
                    self._json_response(HTTPStatus.NOT_FOUND, {"error": str(error)})
                return
            if path.startswith("/api/model/image/") and path.endswith(".png"):
                chip_id = unquote(path.removeprefix("/api/model/image/").removesuffix(".png"))
                try:
                    self._content_response(
                        HTTPStatus.OK,
                        run_catalog.review_chip_bundle(run_id, chip_id).chip_image(chip_id),
                        "image/png",
                    )
                except KeyError:
                    self.send_error(HTTPStatus.NOT_FOUND)
                return
            super().do_GET()

        def do_PUT(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path == '/api/species-debugger/label':
                origin=self.headers.get('Origin')
                if self.headers.get('X-Studio-Species')!='1' or (origin and urlsplit(origin).netloc!=self.headers.get('Host')):
                    self._json_response(HTTPStatus.FORBIDDEN,{'error':'Same-origin UI required'})
                    return
                try:
                    query=self._query(parsed);context=context_for_request(query);payload=self._read_payload()
                    with review_state_lock:
                        manifest,state=read_collection(context.directory)
                        target=species_debugger.label_target(manifest,state,query.get('chip',[''])[0],query.get('tree',[''])[0])
                        patch=species_debugger.label_patch(target,payload)
                        apply_scene_patch(context.directory,target['scene'],patch)
                        snapshot_worker.request(workspace_key(context))
                        manifest,state=read_collection(context.directory)
                        result=species_debugger.label_target(manifest,state,query.get('chip',[''])[0],query.get('tree',[''])[0])
                    self._json_response(HTTPStatus.OK,result)
                except ReviewStateConflictError as error:
                    self._json_response(HTTPStatus.CONFLICT,{'error':str(error)})
                except (ValueError,KeyError,OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST,{'error':str(error)})
                return
            if parsed.path == '/api/recombinant':
                origin = self.headers.get('Origin')
                if self.headers.get('X-Studio-Recombinant') != '1' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                    self._json_response(HTTPStatus.FORBIDDEN, {'error': 'Same-origin UI required'})
                    return
                from urban_tree_ml import recombinant
                try:
                    query = self._query(parsed)
                    root = recombinant.scoped(default_context.config.paths.root, query.get('source', [None])[0])
                    result = recombinant.save(root, self._read_payload())
                    self._json_response(HTTPStatus.OK, result)
                except ReviewStateConflictError as error:
                    self._json_response(HTTPStatus.CONFLICT, {'error': str(error)})
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if parsed.path == '/api/chip-tags':
                origin = self.headers.get('Origin')
                if self.headers.get('X-Studio-Tags') != '1' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                    self._json_response(HTTPStatus.FORBIDDEN, {'error': 'Same-origin UI required'})
                    return
                try:
                    query = self._query(parsed)
                    run = query.get('run', [None])[0]
                    context = context_for_request(query, run)
                    bundle = run_catalog.review_chip_bundle(run, query.get('chip', [''])[0])
                    if not _context_matches_bundle(context, bundle):
                        raise ValueError('Model does not share this curation imagery')
                    payload = self._read_payload()
                    with review_state_lock:
                        result = chip_tags.save_tags(context, bundle, query.get('chip', [''])[0], payload)
                    self._json_response(HTTPStatus.OK, result)
                except ReviewStateConflictError as error:
                    self._json_response(HTTPStatus.CONFLICT, {'error': str(error)})
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if parsed.path not in {"/api/reviews", "/api/review-scene"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            started = perf_counter()
            phases = {}
            save_city = None
            outcome = 'error'
            try:
                context = context_for_request(self._query(parsed))
                save_city = context.city
                payload = self._read_payload()
                if "base_revision" not in payload:
                    raise ReviewStateConflictError(
                        "this review page predates safe saves; refresh it before editing"
                    )
                waiting = perf_counter()
                phases['request_prepare'] = (waiting-started)*1000
                with review_state_lock:
                    acquired = perf_counter()
                    phases['lock_wait'] = (acquired-waiting)*1000
                    result = (apply_scene_patch(context.directory, self._query(parsed).get('scene', [''])[0], payload, timings=phases)
                              if parsed.path == '/api/review-scene' else persist_review_payload(context.directory, payload))
                    snapshot_worker.request(workspace_key(context))
                    result['snapshot_pending'] = True
                    phases['lock_held'] = (perf_counter()-acquired)*1000
                outcome = 'saved'
                result['timing_ms'] = {k: round(v, 2) for k, v in phases.items()}
                self._json_response(HTTPStatus.OK, result)
            except ReviewStateConflictError as error:
                outcome = 'conflict'
                self._json_response(HTTPStatus.CONFLICT, {"error": str(error)})
            except (KeyError, OSError, ValueError, json.JSONDecodeError) as error:
                self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            finally:
                timing = save_timings.record(save_city, parsed.path, outcome, started, phases)
                if timing['total_ms'] >= 1000:
                    print('Slow review save: ' + json.dumps(timing), flush=True)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            if parsed.path in {"/api/street/identify", "/api/street/save"}:
                origin = self.headers.get("Origin")
                if self.headers.get("X-Studio-Species") != "1" or (origin and urlsplit(origin).netloc != self.headers.get("Host")):
                    self._json_response(HTTPStatus.FORBIDDEN, {"error": "Same-origin UI required"})
                    return
                try:
                    payload = self._read_payload()
                    city = self._query(parsed)["city"][0]
                    context = contexts[city]
                    if parsed.path.endswith("/save"):
                        result = street_examples.save(context.config.paths.root, city, payload)
                    else:
                        result = street_examples.identify(payload)
                    self._json_response(HTTPStatus.OK, result)
                except (KeyError, ValueError, TypeError) as exc:
                    self._json_response(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                except RuntimeError as exc:
                    self._json_response(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})
                except Exception:
                    self._json_response(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "Could not process crop. Try again."})
                return
            if parsed.path == '/api/species-debugger/prepare':
                origin = self.headers.get('Origin')
                if self.headers.get('X-Studio-Species') != '1' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                    self._json_response(HTTPStatus.FORBIDDEN, {'error': 'Same-origin UI required'})
                    return
                try:
                    query = self._query(parsed)
                    context = context_for_request(query)
                    chip, tree = query.get('chip', [''])[0], query.get('tree', [''])[0]
                    with review_state_lock:
                        manifest, state = read_collection(context.directory)
                        existing = any(s.get('validation_chip_id') == chip for s in manifest['scenes'])
                        if existing:
                            target = species_debugger.label_target(manifest, state, chip, tree)
                    if not existing:
                        bundle = run_catalog.review_chip_bundle(query.get('image_run', [''])[0], chip)
                        split = bundle.metrics.get('split')
                        if split not in ('train', 'validation') or not _context_matches_bundle(context, bundle):
                            raise ValueError('Choose development predictions for this city')
                        truth = bundle.truth_for_chip(chip)
                        if tree not in truth['tree_id'].cast(str):
                            raise ValueError('Tree is not in the selected development tile')
                        with review_state_lock:
                            # Recheck after model loading: another tab may have prepared it.
                            manifest, state = read_collection(context.directory)
                            if not any(s.get('validation_chip_id') == chip for s in manifest['scenes']):
                                append_validation_chip_to_registration_review(context.config, context.raster,
                                    context.directory, chip, truth, id_chip_pixels=bundle.config.imagery.chip_pixels,
                                    _render_gallery=False)
                                snapshot_worker.request(workspace_key(context))
                            manifest, state = read_collection(context.directory)
                            target = species_debugger.label_target(manifest, state, chip, tree)
                    self._json_response(HTTPStatus.OK, target)
                except ReviewStateConflictError as error:
                    self._json_response(HTTPStatus.CONFLICT, {'error': str(error)})
                except (ValueError, KeyError, OSError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if parsed.path == '/api/backup':
                # Require our same-origin UI request, not a cross-site form POST.
                origin = self.headers.get('Origin')
                if (self.headers.get('X-Studio-Backup') != '1'
                        or (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
                    self._json_response(HTTPStatus.FORBIDDEN, {'error': 'Same-origin UI required'})
                    return
                try:
                    context = context_for_request(self._query(parsed))
                    started = backup_job.start(context.config.paths.root, context.config.paths.annotations)
                    self._json_response(HTTPStatus.ACCEPTED if started else HTTPStatus.CONFLICT,
                                        {'started': started, 'error': None if started else 'Backup already running'})
                except (OSError, ValueError, KeyError) as error:
                    self._json_response(HTTPStatus.BAD_REQUEST, {'error': str(error)})
                return
            if parsed.path != "/api/finalize":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if self._query(parsed).get('city') == ['all']:
                from urban_tree_ml.city_scope import publish_cities
                # Serialize with normal saves/snapshots, publish saved state only.
                # Expose each city's outcome: partial success is never reported as all-success.
                with snapshot_worker.snapshot_lock, review_state_lock:
                    result = publish_cities(contexts, lambda owner: finalize_registration_feedback(
                        owner.config, owner.raster, review_dir=owner.directory))
                self._json_response(HTTPStatus.OK, result)
                return
            try:
                context = context_for_request(self._query(parsed))
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
    snapshot_worker = SnapshotWorker(all_contexts, review_state_lock)
    backup_job = BackupJob(before_backup=snapshot_worker.flush, snapshot_lock=snapshot_worker.snapshot_lock)
    # Canonical reviews survive forced shutdown; rebuild any interrupted snapshots on startup.
    for city, context in all_contexts.items():
        if (context.directory / 'reviews.json').exists():
            snapshot_worker.request(city)
    handler = partial(ReviewHandler, directory=str(default_context.directory))
    server = ThreadingHTTPServer((bind, port), handler)
    # server_close waits for in-flight review writes before closing the snapshot worker.
    server.daemon_threads = False
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
