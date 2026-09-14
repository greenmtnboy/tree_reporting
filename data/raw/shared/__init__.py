"""Shared ingest library.

Everything a city ingest reaches for that is not the city's own field
mapping: the canonical Arrow schema and the registries beside it
(`ingest`), the Overpass extraction every `osm-{code}` job runs (`osm`),
the ecoregion service's addresses (`ecoregions`), one reader per open-data
platform this repo talks to more than twice (`platforms`), and one
common-name to accepted-binomial table per language (`species`).

A city script reaches them the way it always has -- `data/raw` on the path,
then an ordinary import:

    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from shared.ingest import emit, enforce_tree_schema

These modules carried a `_shared` suffix and a leading underscore while they
sat loose beside the forty city directories, which is what marked them as
"not a city". The package does that now.
"""
