#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy>=0.3.391", "pydantic", "google-cloud-storage"]
# ///
"""Command-line front end to the enrichment admin.

The same backend as the form (`backend.py`): the same checks, the same
staging file, the same publish that patches the live table and every alias
row of the taxon.  It is for a correction you can name without looking at
photos -- a wrong shade, a growth form, a common name -- and for an agent
making one.

    cd data/raw
    uv run enrichment/admin/cli.py show "Pinus sylvestris"
    uv run enrichment/admin/cli.py search pine --limit 20
    uv run enrichment/admin/cli.py set "Pinus sylvestris" foliage_colors=#4d7466 trunk_color=#9a5a3c
    uv run enrichment/admin/cli.py pending
    uv run enrichment/admin/cli.py publish            # asks; --yes to skip the prompt
    uv run enrichment/admin/cli.py discard "Pinus sylvestris"

`set` takes `field=value` pairs and changes only the fields it names.  A
value is read the way the form reads a text box: a list column splits on
commas (`common_names="Scots pine, Scotch pine"`), and an empty value
(`trunk_color=`) clears the field.  `field:=<json>` passes a JSON value
instead, for a list whose items contain commas.

An edit is staged, not published: the form shows it as pending, and either
front end publishes it.  Publishing needs `gcloud auth application-default
login`; everything else reads the public table.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from enrichment.admin.backend import (  # noqa: E402
    EDITABLE,
    ENRICHMENT_GCS_URI,
    GROUPS,
    AdminState,
    ValidationError,
)


def parse_assignments(pairs: list[str]) -> dict:
    """`field=value` and `field:=json` pairs as a dict of changes."""
    changes: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValidationError([f"{pair!r}: expected field=value"])
        name, value = pair.split("=", 1)
        if name.endswith(":"):
            name = name[:-1]
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValidationError([f"{name}: not JSON ({exc.msg})"]) from exc
        name = name.strip()
        if name in changes:
            raise ValidationError([f"{name}: given twice"])
        changes[name] = value
    return changes


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value) or "-"
    return str(value)


def print_row(detail: dict, out=None) -> None:
    out = out or sys.stdout
    row = detail["row"]
    flags = [f for f, on in (("pending", detail["pending"]), ("sentinel", detail["sentinel"])) if on]
    if detail["alias_of"]:
        flags.append(f"alias of {detail['alias_of']}")
    print(f"{detail['species']}" + (f"  [{', '.join(flags)}]" if flags else ""), file=out)
    if detail.get("aliases"):
        print(f"  also published as: {', '.join(detail['aliases'])}", file=out)
    changed = set(detail["changed"])
    published = detail["published"] or {}
    for group, names in GROUPS:
        print(f"\n{group}", file=out)
        for name in names:
            mark = "*" if name in changed else " "
            line = f" {mark} {name:<26} {_fmt(row.get(name))}"
            if name in changed:
                line += f"   (published: {_fmt(published.get(name))})"
            print(line, file=out)


def print_changes(state: AdminState, out=None) -> int:
    """Each staged edit, field by field against what is published."""
    out = out or sys.stdout
    if not state.edits:
        print("nothing staged", file=out)
        return 0
    for species in sorted(state.edits):
        detail = state.detail(species)
        print(species, file=out)
        published = detail["published"] or {}
        for name in detail["changed"]:
            print(f"  {name}: {_fmt(published.get(name))} -> {_fmt(detail['row'].get(name))}", file=out)
        if detail["aliases"]:
            print(f"  (also written to {', '.join(detail['aliases'])})", file=out)
    return len(state.edits)


def main(argv: list[str] | None = None, state: AdminState | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("show", help="one species' row, staged edits marked *")
    p.add_argument("species")
    p.add_argument("--json", action="store_true", help="the row as JSON")

    p = sub.add_parser("search", help="find species by name, common name or synonym")
    p.add_argument("query", nargs="?", default="")
    p.add_argument("--filter", default="", choices=["", "pending", "incomplete", "nophoto", "notrunk", "nodesc"])
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--counts", action="store_true", help="read tree counts from the rollup and order by them")

    p = sub.add_parser("set", help="stage a change to named fields of one species")
    p.add_argument("species")
    p.add_argument("assignments", nargs="+", metavar="field=value")

    p = sub.add_parser("apply", help="stage changes to many species from a JSON file, all or none")
    p.add_argument("file", type=Path, help='{"<species>": {"<field>": <value>, ...}, ...}')

    sub.add_parser("pending", help="what is staged, against what is published")

    p = sub.add_parser("discard", help="drop a staged edit")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("species", nargs="?")
    g.add_argument("--all", action="store_true")

    p = sub.add_parser("publish", help="patch the staged edits into the live table and upload it")
    p.add_argument("--yes", action="store_true", help="do not ask first")

    args = parser.parse_args(argv)
    if state is None:
        state = AdminState()
        state.load(counts=getattr(args, "counts", False))

    try:
        if args.command == "show":
            detail = state.detail(args.species)
            if detail is None:
                print(f"no species {args.species!r}", file=sys.stderr)
                return 1
            if args.json:
                row = {k: detail["row"].get(k) for k in ["species", *EDITABLE]}
                print(json.dumps(row, indent=1, ensure_ascii=False))
            else:
                print_row(detail)
        elif args.command == "search":
            for s in state.search(args.query, args.filter, args.limit, "trees" if args.counts else "name"):
                bits = [s["common_name"] or "-", s["tree_form"] or "-"]
                if args.counts:
                    bits.append(f"{s['trees']:,} trees")
                if s["pending"]:
                    bits.append("pending")
                print(f"{s['species']:<40} {' | '.join(bits)}")
        elif args.command == "set":
            detail = state.patch(args.species, parse_assignments(args.assignments))
            if detail["pending"]:
                published = detail["published"] or {}
                print(f"staged {args.species}:")
                for name in detail["changed"]:
                    print(f"  {name}: {_fmt(published.get(name))} -> {_fmt(detail['row'].get(name))}")
                print("publish with: uv run enrichment/admin/cli.py publish")
            else:
                print(f"{args.species}: same as published; nothing staged")
        elif args.command == "apply":
            changes = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(changes, dict) or not all(isinstance(v, dict) for v in changes.values()):
                raise ValidationError([f"{args.file}: expected an object of species -> {{field: value}}"])
            details = state.patch_many(changes)
            unchanged = sorted(s for s, d in details.items() if not d["pending"])
            print(f"staged {len(details) - len(unchanged)} species" + (f"; same as published: {', '.join(unchanged)}" if unchanged else ""))
            print("review with `pending`, publish with `publish`")
        elif args.command == "pending":
            print_changes(state)
        elif args.command == "discard":
            if args.all:
                state.discard_all()
            else:
                state.discard(args.species)
            print_changes(state)
        elif args.command == "publish":
            state._sync_edits()
            if not print_changes(state):
                return 1
            if not args.yes:
                if not sys.stdin.isatty():
                    print("not a terminal; pass --yes to publish", file=sys.stderr)
                    return 1
                answer = input(f"publish {len(state.edits)} row(s) to {ENRICHMENT_GCS_URI}? [y/N] ")
                if answer.strip().lower() not in ("y", "yes"):
                    print("not published")
                    return 1
            summary = state.publish()
            print(
                f"published {summary['rows']} rows: replaced {', '.join(summary['replaced']) or '-'}; "
                f"aliases {', '.join(summary['aliased']) or '-'}; "
                f"appended {', '.join(summary['appended']) or '-'}"
            )
    except ValidationError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # "Platanus × hispanica" on a Windows console
    raise SystemExit(main())
