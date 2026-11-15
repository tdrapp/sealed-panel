"""The rules of a judge seat: calibration, what a seat may take, the void cap, its lock."""

from __future__ import annotations

import contextlib
import statistics
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import EvalError
from .settings import Config
from .store import locked

# Void attempts allowed per judge slot and round. A void never fills a seat, so voids have
# their own cap and do not count toward max_replacements.
MAX_VOIDS = 3


def calibrate(result: dict[str, Any], meta: dict[str, Any], spec: dict[str, Any]) -> None:
    """Discard a judge whose control is too high or whose known-good is too low."""
    ctrl = result["items"].get("control", {}).get("raw_scores", {})
    if not ctrl:
        result["ok"] = False
        result["errors"].append("the control has no verified score")
    else:
        mean_ctrl = statistics.mean(ctrl.values())
        result["control_mean"] = round(mean_ctrl, 2)
        if mean_ctrl > spec["control_max"]:
            result["ok"] = False
            result["errors"].append(
                f"control scored {mean_ctrl:.2f} > {spec['control_max']}: this judge does "
                "not discriminate; brief a replacement on the same brief file"
            )
    good = result["items"].get("known_good", {}).get("raw_scores", {})
    has_good = "known_good" in meta["key"].values()
    if (
        result["ok"]
        and has_good
        and (not good or statistics.mean(good.values()) < spec["good_min"])
    ):
        result["ok"] = False
        result["errors"].append(
            f"known-good scored below {spec['good_min']}: this judge is too harsh or the "
            "rubric does not fit; brief a replacement"
        )


def check_void_cap(judge: int, prior: list[dict[str, Any]]) -> None:
    """Refuse a further void on a slot that already has MAX_VOIDS of them."""
    voids = [r for r in prior if r.get("void")]
    if len(voids) >= MAX_VOIDS:
        raise EvalError(
            f"judge {judge} already has {len(voids)} voided attempts: find why its agents stop "
            "before voiding more"
        )


def check_seat(judge: int, prior: list[dict[str, Any]], source: str, cfg: Config) -> None:
    """Refuse a reply the slot cannot take: a second valid one, a pasted one where the brief
    may have been seen, or one past the replacement cap."""
    if any(r["ok"] for r in prior):
        raise EvalError(
            f"judge {judge} already has a valid reply in this round: "
            "a second one would be a re-roll"
        )
    if source == "output" and any(r.get("void") and r.get("replied") for r in prior):
        raise EvalError(
            f"judge {judge} already had a reply voided (brief not verbatim): its next reply must "
            "come from a transcript holding the pasted brief, never pasted by hand"
        )
    real = [r for r in prior if not r.get("void")]
    if len(real) > cfg.max_replacements:
        raise EvalError(
            f"judge {judge} was rejected {len(real)} times: stop and fix the brief or the rubric"
        )


@contextlib.contextmanager
def seat_lock(rdir: Path, judge: int) -> Iterator[None]:
    """Hold a seat of a round (an flock on `seat_j<J>.lock` beside its brief) while the block
    runs: every call that records an attempt on the seat holds it from the seat check to the
    stored attempt, so two calls never both pass the check. Raises SeatTaken when another call
    holds it.

    Args:
        rdir: The round directory.
        judge: The judge slot.
    """
    busy = f"seat {judge} is held by another call (a judge running or a reply being recorded)"
    with locked(rdir / f"seat_j{judge}.lock", busy):
        yield
