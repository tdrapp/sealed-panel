"""DEMO ONLY: play the judges of one round so the quick start runs without a model.

This script reads a run's keys.json and the texts of one round, and writes one reply per
brief with fixed scores: on the candidate (and on the round-1 baseline of a sealed panel)
`ask` 5, `relevance` 4 with a material flaw, `evidence` 5; on the control 1 everywhere. The
replies say nothing about the text. Never record them in a real evaluation: a real reply
comes from a fresh judge that read the brief.

    python examples/announcement/play_judges.py RUN --round N [--sealed] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

GOOD = {"ask": 5, "relevance": 4, "evidence": 5}
FLAWS = {"relevance": "material"}
WEAKEST = ("relevance", "The risk to the release train is only implied by the opt-out flag.")


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip()[:60] for p in parts if len(p.strip()) >= 25]


def reply_for(
    meta: dict[str, Any], texts: dict[str, str], dims: list[str], tag: str
) -> dict[str, Any]:
    items = {}
    for label, role in meta["key"].items():
        quotes = sentences(texts[role])
        bad = role == "control"
        cells = {}
        for i, did in enumerate(dims):
            cells[did] = {
                "reasoning": "Demo reply: fixed score, not a reading of the text.",
                "quote": quotes[i % len(quotes)],
                "severity": "blocking" if bad else FLAWS.get(did, "none"),
                "score": 1 if bad else GOOD.get(did, 4),
            }
        items[label] = {
            "retell": f"Demo reply {tag}_j{meta['judge']}.",
            "dimensions": cells,
            "weakest_point": {"text": WEAKEST[1], "dimension": WEAKEST[0], "quote": quotes[0]},
        }
    return {"items": items}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DEMO ONLY: write fixed judge replies for a round.")
    ap.add_argument("run", type=Path)
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--sealed", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("replies"))
    a = ap.parse_args(argv)
    rdir = a.run / ("sealed" if a.sealed else "rounds") / f"r{a.round}"
    keys = json.loads((rdir / "keys.json").read_text(encoding="utf-8"))
    spec = json.loads((a.run / "spec.json").read_text(encoding="utf-8"))
    a.out.mkdir(parents=True, exist_ok=True)
    prefix = f"{'s' if a.sealed else 'r'}{a.round}"
    for meta in keys["judges"]:
        dims = [
            d["id"]
            for d in spec["dimensions"]
            if not d.get("personas") or meta["persona"] in d["personas"]
        ]
        texts = {r: (rdir / f"{r}.md").read_text(encoding="utf-8") for r in meta["key"].values()}
        path = a.out / f"{prefix}_j{meta['judge']}.json"
        path.write_text(
            json.dumps(reply_for(meta, texts, dims, prefix), indent=2) + "\n", encoding="utf-8"
        )
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
