"""Views derived from the files of a run, never from a shared mutable list."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .store import load_json, read_text

REPLY_GLOB = "*/r*/out_j*.json"
# A round's directory: rounds/rN for the loop, sealed/rN for the sealed panel.
ROUND_DIR = re.compile(r"r[1-9][0-9]*")


def _recorded_field(run: Path, field: str) -> set[str]:
    found = set()
    for f in run.glob(REPLY_GLOB):
        try:
            found.add(load_json(f)["result"][field])
        except (KeyError, ValueError):
            continue
    return found


def used_agent_ids(run: Path) -> set[str]:
    """Every agent id recorded in the run.

    Args:
        run: The run directory.
    """
    return _recorded_field(run, "agent_id")


def used_reply_hashes(run: Path) -> set[str]:
    """The sha256 of every reply recorded in the run.

    Args:
        run: The run directory.
    """
    return _recorded_field(run, "reply_sha256")


def results_by_judge(rdir: Path) -> dict[int, list[dict[str, Any]]]:
    """Every recorded reply of one round, per judge slot, in the order they were recorded."""
    by_judge: dict[int, list[dict[str, Any]]] = {}
    for f in sorted(rdir.glob("out_j*.json"), key=lambda p: p.stat().st_mtime_ns):
        r = load_json(f)["result"]
        by_judge.setdefault(r["judge"], []).append(r)
    return by_judge


def rounds(run: Path) -> list[dict[str, Any]]:
    """Every aggregated round of the run, read from its `result.json`, loop rounds first, each
    group by round number. The round files are the only record of a round: no list is kept
    beside them.

    Args:
        run: The run directory.
    """
    found = [
        load_json(d / "result.json")
        for group in ("rounds", "sealed")
        for d in (run / group).glob("r*")
        if ROUND_DIR.fullmatch(d.name) and (d / "result.json").is_file()
    ]
    return sorted(found, key=lambda r: (bool(r.get("sealed", False)), int(r["round"])))


def view(run: Path) -> dict[str, Any]:
    """The run's ledger, derived: the rubric hash from `rubric.lock` and the aggregated rounds.
    It replaces `ledger.json`, which 0.4.0 no longer writes (a run frozen before still has one,
    and it agrees with its round files: 0.4.0 never writes to such a run).

    Args:
        run: The run directory.
    """
    return {"rubric_sha256": read_text(run / "rubric.lock").strip(), "rounds": rounds(run)}


def other_runs(run: Path) -> list[str]:
    """Names of the other frozen runs beside this one."""
    return sorted(
        p.name
        for p in run.parent.iterdir()
        if p != run and p.is_dir() and (p / "rubric.lock").exists()
    )
