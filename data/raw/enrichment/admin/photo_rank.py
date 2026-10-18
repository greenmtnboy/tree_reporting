#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["torch", "transformers", "pillow", "requests"]
# ///
"""Score iNaturalist candidate photos by what they show: a whole tree, a leaf, ...

The admin's photo pickers hand this the candidate URLs on stdin
(`{"urls": [...]}`) and get back, on stdout,

    {"labels": [...], "scores": {url: {label: p}}, "duplicate_of": {url: url}}

where each photo's `p` sum to 1 over `labels`, and `duplicate_of` maps a
re-upload (or near-identical burst shot) to the first copy in input order.

It is zero-shot CLIP (ViT-L/14): every photo is compared against a few text
prompts per label, softmaxed over all prompts, and summed per label.  The
labels compete with each other on purpose -- `young`, `crowd`, `crown_up` and
`other` exist to soak up what otherwise scores as a whole tree (a seedling, a
forest, a canopy shot from below, an information board).  On the 2026-10-16
pilot over the top-100 species, the reviewer's hand-picked whole-tree photo
ranked in the top six of its species' ~100 candidates for 11 of 16 species,
and the species' own leaf/fruit photo scored ~0 as a whole tree.

Runs on CPU.  The model (~1.7 GB) downloads to the Hugging Face cache on first
use; small (240 px) copies of the photos and the scores are cached under
`.photo_rank_cache/` (gitignored), keyed by the prompt set, so re-ranking a
species is instant and a prompt change re-scores everything.  Batch size and
thread count are kept low: a larger batch ran a 32 GB laptop out of memory.

    echo '{"urls": ["https://.../photos/1/medium.jpg"]}' | uv run enrichment/admin/photo_rank.py
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / ".photo_rank_cache"
MODEL = "openai/clip-vit-large-patch14"
BATCH = int(os.environ.get("PHOTO_RANK_BATCH", 8))
THREADS = int(os.environ.get("PHOTO_RANK_THREADS", 8))
USER_AGENT = "urban-tree-reporting enrichment-admin photo ranker"
# Hamming distance (of 256 bits) under which two photos count as the same picture.
DUPLICATE_BITS = 20

# label -> prompts.  Order is the display order of the admin's sort menu.
PROMPTS: dict[str, list[str]] = {
    # the trunk slot's target: one tree, seen whole -- crown and trunk in frame
    "tree": [
        "a photo of a whole single tree showing its full crown and trunk",
        "a photo of a lone tree standing in a field",
        "a photo of a single street tree in a city, seen from the pavement",
        "a photo of a large tree in a park, photographed from a distance",
        "a photo of a single tree in full blossom",
        "a photo of a single tree with autumn foliage",
    ],
    "leaf": [
        "a close-up photo of green tree leaves",
        "a photo of a single leaf held in a hand",
        "a photo of the underside of a leaf",
        "a close-up photo of autumn coloured leaves on a branch",
    ],
    "flower": ["a close-up photo of flowers on a branch", "a close-up photo of flowers on bare twigs"],
    "fruit": ["a close-up photo of fruit, seeds or nuts on a branch", "a photo of seed pods hanging from a branch"],
    "bark": [
        "a close-up photo of the bark of a tree trunk",
        "a photo of a tree trunk filling the frame",
        "a photo of the base of a tree trunk",
    ],
    "twig": ["a close-up photo of a bare twig with winter buds"],
    "crown_up": ["a photo looking straight up into the branches of a tree canopy"],
    "crowd": [
        "a photo of a dense forest with many trees",
        "a photo of a street lined with a row of many trees",
    ],
    "young": [
        "a photo of a small seedling growing from the ground",
        "a photo of a young sapling with a few leaves",
        "a photo of a seedling growing in a crack in the pavement",
    ],
    "other": [
        "a photo of fallen leaves and seeds on the ground",
        "a photo of a pressed herbarium specimen",
        "a photo of a tape measure wrapped around a tree",
        "a photo of ivy and plants growing on the forest floor",
        "a photo of a sign or information board with text",
    ],
}
PROMPT_VERSION = hashlib.sha1(json.dumps([MODEL, PROMPTS]).encode()).hexdigest()[:12]


def small_url(url: str) -> str:
    """The 240 px rendition of an iNat photo URL -- plenty for a 224 px model."""
    return re.sub(r"/(square|medium|large|original)\.", "/small.", url)


def image_path(url: str) -> Path:
    return CACHE / "img" / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".jpg")


def aggregate(labels: list[str], probs: list[float]) -> dict[str, float]:
    """Sum per-prompt probabilities into per-label ones, in PROMPTS order."""
    out = {label: 0.0 for label in PROMPTS}
    for label, p in zip(labels, probs):
        out[label] += p
    return {k: round(v, 4) for k, v in out.items()}


def dhash(path: Path) -> int | None:
    from PIL import Image

    try:
        im = Image.open(path).convert("L").resize((17, 16))
    except Exception:  # noqa: BLE001 -- an unreadable download is just not deduped
        return None
    px = im.tobytes()
    bits = 0
    for r in range(16):
        for c in range(16):
            bits = (bits << 1) | (px[r * 17 + c] > px[r * 17 + c + 1])
    return bits


def duplicates(urls: list[str], hashes: dict[str, int | None]) -> dict[str, str]:
    """Map each later near-copy to the first photo, in input order, it repeats."""
    kept: list[tuple[str, int]] = []
    dup: dict[str, str] = {}
    for u in urls:
        h = hashes.get(u)
        if h is None:
            continue
        first = next((k for k, kh in kept if bin(h ^ kh).count("1") <= DUPLICATE_BITS), None)
        if first is None:
            kept.append((u, h))
        else:
            dup[u] = first
    return dup


def download(urls: list[str]) -> None:
    import requests

    (CACHE / "img").mkdir(parents=True, exist_ok=True)
    todo = [u for u in urls if not image_path(u).exists()]
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def one(u: str) -> None:
        try:
            r = session.get(small_url(u), timeout=30)
            if r.ok:
                image_path(u).write_bytes(r.content)
        except requests.RequestException as exc:
            print(f"[rank] skip {u}: {exc}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(one, todo))


def _embedding(x):
    # transformers 5 returns a model output from get_*_features; 4 returned the tensor
    return x if hasattr(x, "norm") else x.pooler_output


def score(urls: list[str], cached: dict[str, dict]) -> dict[str, dict]:
    todo = [u for u in urls if u not in cached and image_path(u).exists()]
    if not todo:
        return cached
    import torch
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor

    torch.set_num_threads(THREADS)
    model = CLIPModel.from_pretrained(MODEL).eval()
    proc = CLIPProcessor.from_pretrained(MODEL)
    labels = [label for label, ps in PROMPTS.items() for _ in ps]
    texts = [p for ps in PROMPTS.values() for p in ps]
    with torch.no_grad():
        t = _embedding(model.get_text_features(**proc(text=texts, return_tensors="pt", padding=True)))
        t = t / t.norm(dim=-1, keepdim=True)
        for i in range(0, len(todo), BATCH):
            batch, images = [], []
            for u in todo[i:i + BATCH]:
                try:
                    images.append(Image.open(image_path(u)).convert("RGB"))
                    batch.append(u)
                except Exception:  # noqa: BLE001
                    print(f"[rank] unreadable {u}", file=sys.stderr)
            if not images:
                continue
            v = _embedding(model.get_image_features(**proc(images=images, return_tensors="pt")))
            v = v / v.norm(dim=-1, keepdim=True)
            for u, pr in zip(batch, (100.0 * v @ t.T).softmax(dim=-1).tolist()):
                cached[u] = aggregate(labels, pr)
            print(f"[rank] {min(i + BATCH, len(todo))}/{len(todo)}", file=sys.stderr)
    return cached


def main() -> int:
    urls = [u for u in dict.fromkeys(json.load(sys.stdin).get("urls") or []) if isinstance(u, str)]
    CACHE.mkdir(exist_ok=True)
    scores_path = CACHE / f"scores_{PROMPT_VERSION}.json"
    cached = json.loads(scores_path.read_text()) if scores_path.exists() else {}
    download(urls)
    cached = score(urls, cached)
    scores_path.write_text(json.dumps(cached))
    present = [u for u in urls if u in cached]
    json.dump({
        "labels": list(PROMPTS),
        "scores": {u: cached[u] for u in present},
        "duplicate_of": duplicates(present, {u: dhash(image_path(u)) for u in present}),
    }, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
