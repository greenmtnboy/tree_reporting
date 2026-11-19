"""The enrichment table's hand-correction tools.

`backend.py` is the admin itself: validation, staging, the alias-aware patch
and the publish. Two front ends drive it and know nothing of each other:
`server.py`, a localhost form (`index.html` is its page), and `cli.py`, the
same edits from a command line. It is a workstation tool, excluded from the
workspace bundle: no job runs it, and publishing needs
`gcloud auth application-default login`.

    cd data/raw && uv run enrichment/admin/server.py
    cd data/raw && uv run enrichment/admin/cli.py --help
"""
