"""Views derived from the files of a run, never from a shared mutable list."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .store import load_json

REPLY_GLOB = "*/r*/out_j*.json"


def _recorded_field(run: Path, field: str) -> set[str]:
    found = set()
    for f in run.glob(REPLY_GLOB):
        try:
            found.add(load_json(f)["result"][field])
        except (KeyError, ValueError):
            continue
    return found


def used_agent_ids(run: Path) -> set[str]:
    return _recorded_field(run, "agent_id")


def used_reply_hashes(run: Path) -> set[str]:
    return _recorded_field(run, "reply_sha256")


def results_by_judge(rdir: Path) -> dict[int, list[dict[str, Any]]]:
    """Every recorded reply of one round, per judge slot, in the order they were recorded."""
    by_judge: dict[int, list[dict[str, Any]]] = {}
    for f in sorted(rdir.glob("out_j*.json"), key=lambda p: p.stat().st_mtime_ns):
        r = load_json(f)["result"]
        by_judge.setdefault(r["judge"], []).append(r)
    return by_judge


def other_runs(run: Path) -> list[str]:
    """Names of the other frozen runs beside this one."""
    return sorted(
        p.name
        for p in run.parent.iterdir()
        if p != run and p.is_dir() and (p / "rubric.lock").exists()
    )
