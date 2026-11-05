"""A stub judge for the runner tests: reads one brief on stdin, prints one JSON reply.

The reply has fixed scores: 1 on an item holding the low word (the control of the test texts),
4 elsewhere, every quote taken from the item itself. Its retell carries the sha256 of the
bytes read, so a test can check the brief arrived unchanged. Options make it misbehave:

    stub_judge.py [--exit N] [--sleep S] [--silent] [--noise] [--show-env] [--show-cwd]
                  [--low WORD] [--score N] [--fixed] [--fail-first PATH]

Without --fixed, the retell also carries a random tag: real judges never reply identically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import time

ITEM = re.compile(r'<item label="([A-Z])">\n(.*?)\n</item>', re.DOTALL)
DIMENSION = re.compile(r"^### ([A-Za-z0-9_-]+):", re.MULTILINE)


def reply(brief: str, tag: str, low: str, score: int) -> dict[str, object]:
    dims = DIMENSION.findall(brief)
    items: dict[str, object] = {}
    for label, text in ITEM.findall(brief):
        bad = low in text
        quote = text.strip()[:40]
        items[label] = {
            "retell": f"stub {tag}",
            "dimensions": {
                d: {
                    "reasoning": "stub reading",
                    "quote": quote,
                    "severity": "blocking" if bad else "none",
                    "score": 1 if bad else score,
                }
                for d in dims
            },
            "weakest_point": {"text": "stub", "dimension": dims[0], "quote": quote},
        }
    return {"items": items}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.0)
    ap.add_argument("--silent", action="store_true")
    ap.add_argument("--noise", action="store_true")
    ap.add_argument("--show-env", action="store_true")
    ap.add_argument("--show-cwd", action="store_true")
    ap.add_argument("--low", default="synergies")
    ap.add_argument("--score", type=int, default=4)
    ap.add_argument("--fixed", action="store_true")
    ap.add_argument("--fail-first", help="exit 3 when PATH does not exist yet, creating it")
    a = ap.parse_args()
    data = sys.stdin.buffer.read()
    digest = hashlib.sha256(data).hexdigest()
    if a.noise:
        print("stub noise {not json} on stderr", file=sys.stderr)
    if a.show_env:
        print("ENV " + json.dumps(sorted(os.environ)), file=sys.stderr)
    if a.show_cwd:
        print("CWD " + json.dumps([os.getcwd(), sorted(os.listdir("."))]), file=sys.stderr)
    if not a.silent:
        tag = digest if a.fixed else f"{digest} {secrets.token_hex(4)}"
        print(json.dumps(reply(data.decode("utf-8"), tag, a.low, a.score)), flush=True)
    if a.fail_first and not os.path.exists(a.fail_first):
        open(a.fail_first, "w").close()
        return 3
    if a.sleep:
        time.sleep(a.sleep)
    return a.exit


if __name__ == "__main__":
    sys.exit(main())
