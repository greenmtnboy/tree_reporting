#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy>=0.3.391", "requests"]
# ///
"""Refresh dashboard summaries when the full inventory or enrichment republishes."""
from prediction_inputs_probe import newest_input_publication
from shared.ingest import emit_freshness


if __name__ == "__main__":
    emit_freshness(None, newest_input_publication, label="dashboard summary inputs")
