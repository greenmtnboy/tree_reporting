"""The enrichment table's localhost admin form.

`server.py` is the whole thing -- a `ThreadingHTTPServer` over the
published species parquet, `index.html` the single page it serves. It is a
workstation tool, excluded from the workspace bundle: no job runs it, and
publishing needs `gcloud auth application-default login`.

    cd data/raw && uv run enrichment/admin/server.py
"""
