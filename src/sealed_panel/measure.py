"""Record one instrument reading for a round."""

from __future__ import annotations

from pathlib import Path

from .errors import EvalError
from .run import load_frozen, run_config, writes_run
from .store import dump_json, load_json, round_dir, under_run_lock


@writes_run
@under_run_lock
def record_measurement(
    run: Path, rnd: int, mid: str, value: str, passed: bool | None, sealed: bool
) -> bool:
    """Record one instrument reading. A numeric threshold decides pass or fail. The round's
    readings are read and rewritten under the run's lock, so concurrent readings are all kept.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        mid: The measured criterion's id.
        value: The reading.
        passed: Pass or fail, for a criterion without a numeric threshold; else None.
        sealed: Whether the round is the sealed panel's.
    """
    spec = load_frozen(run)
    run_config(run)
    m = next((x for x in spec.get("measured") or [] if x["id"] == mid), None)
    if m is None:
        raise EvalError(f"{mid} is not a measured criterion of this rubric")
    if "max" in m or "min" in m:
        if passed is not None:
            raise EvalError(f"{mid} has a numeric threshold: the script decides pass or fail")
        try:
            num = float(value)
        except ValueError as exc:
            raise EvalError(f"{mid} needs a number, got {value!r}") from exc
        passed = ("max" not in m or num <= m["max"]) and ("min" not in m or num >= m["min"])
    elif passed is None:
        raise EvalError(f"{mid} has no numeric threshold: say whether it passed")
    rdir = round_dir(run, rnd, sealed)
    if not (rdir / "keys.json").exists():
        raise EvalError("brief the round before measuring it")
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: measure before aggregating")
    path = rdir / "measured.json"
    rows = load_json(path) if path.exists() else []
    if any(r["id"] == mid for r in rows):
        raise EvalError(f"{mid} is already measured for this round: a measurement is taken once")
    rows = [*rows, {"id": mid, "value": value, "pass": passed}]
    dump_json(path, rows)
    return passed
