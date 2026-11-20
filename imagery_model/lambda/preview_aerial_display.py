"""Read-only, five-minute UI fixture; no annotation or model endpoints."""
import argparse
import base64
import mimetypes
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from urban_tree_ml.aerial_display import AERIAL_DISPLAY_CSS, AERIAL_DISPLAY_JS, AERIAL_DISPLAY_SVG

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8789)
    args = parser.parse_args()
    image = base64.b64encode(args.image.read_bytes()).decode()
    mime = mimetypes.guess_type(args.image.name)[0] or 'image/png'
    html = f'''<!doctype html><html><head><style>
    body {{margin:24px;background:#101713;color:#eef5ef;font:14px system-ui}}
    .card {{width:700px;max-width:90vw}} .card-head {{display:flex;justify-content:space-between;padding:10px}}
    .image-wrap {{position:relative}} .image-wrap img {{width:100%;display:block}}
    .tree-marker {{position:absolute;left:50%;top:50%;width:30px;height:30px;border:2px solid cyan;border-radius:50%}}
    {AERIAL_DISPLAY_CSS}</style></head><body>{AERIAL_DISPLAY_SVG}
    <article class="card"><div class="card-head"><strong>Aerial display · read-only preview</strong></div>
    <div class="image-wrap"><img alt="NYC aerial scene" src="data:{mime};base64,{image}">
    <span class="tree-marker" title="Overlay color stays unchanged"></span></div></article>
    <script>const pageParameters=new URLSearchParams(location.search),metadata={{city:'preview'}};
    {AERIAL_DISPLAY_JS}
    document.querySelector('.card-head').append(aerialDisplay.controls());</script></body></html>'''.encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(html)

    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.timeout = 1
    deadline = time.monotonic() + 300
    print(f'Read-only preview http://127.0.0.1:{args.port}', flush=True)
    while time.monotonic() < deadline:
        server.handle_request()
    server.server_close()
