"""Audit a frozen classifier vocabulary against an external inventory.

Coverage means a class can be emitted, not that it is predicted accurately.
No taxonomy expansion, label remapping, or training occurs here.
"""
from __future__ import annotations

import pandas as pd


def species_coverage(frame, taxonomy, *, excluded=("Unknown", "Palm", "Shrub", "Cactus"), top_n=20):
    species = frame["species"].astype("string").str.strip()
    eligible = species.notna() & species.ne("") & ~species.isin(excluded)
    counts = species[eligible].value_counts()
    species_ids = {name: i for i, name in enumerate(taxonomy["species"])}
    genera = set(taxonomy["genera"])
    rows = [
        {"species": str(name), "trees": int(count),
         "species_id": species_ids.get(str(name)),
         "supported_species": str(name) in species_ids,
         "supported_genus": str(name).split()[0] in genera}
        for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]
    known = int(eligible.sum())
    covered = sum(r["trees"] for r in rows if r["supported_species"])
    return {"rows": len(frame), "named_rows": known,
            "unknown_or_excluded_rows": len(frame) - known,
            "covered_named_rows": covered,
            "named_row_coverage": covered / known if known else None,
            "top_species": rows[:top_n],
            "unsupported_species": [r for r in rows if not r["supported_species"]],
            "all_species": rows}
