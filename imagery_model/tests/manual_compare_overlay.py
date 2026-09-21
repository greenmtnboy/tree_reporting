"""Disposable UI fixture; never reads or writes real curation."""
import json
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from copy import deepcopy

from test_model_debug import _debug_fixture
from urban_tree_ml.model_debug import CHIP_COMPARE_HTML

temporary = tempfile.TemporaryDirectory()
bundle, _ = _debug_fixture(Path(temporary.name))
entry = {'available': True, 'run': {'run_id': 'sf-boston-naip-curation-v5-retrain',
         'training_run_id': 'sf-boston-naip-curation-v5-retrain', 'metrics': {'confidence_threshold': .35}},
         'data': bundle.chip('r000000_c000000'), 'display': bundle.summary()['display']}
other = deepcopy(entry)
other['run']['run_id'] = other['run']['training_run_id'] = 'v9'
other['data']['predictions'][0]['output_x'] += 3
payload = {'runs': [entry, other], 'selected_run_id': 'v9', 'chip_id': 'r000000_c000000', 'curation_available': False}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith('/api/runs/chip/'):
            content, mime = json.dumps(payload).encode(), 'application/json'
        elif self.path.startswith('/api/model/image/'):
            content, mime = bundle.chip_image('r000000_c000000'), 'image/png'
        else:
            content, mime = CHIP_COMPARE_HTML.encode(), 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.end_headers()
        self.wfile.write(content)

HTTPServer(('127.0.0.1', 8878), Handler).serve_forever()
