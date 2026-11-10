"""The stop rule, read from the ledger: CONTINUE, CONVERGED, PLATEAU, CAP or SEALED, and
BLOCKED BY FLAG when a flagged candidate would otherwise converge or take the sealed grade."""

from __future__ import annotations

from typing import Any

STOP_DECISIONS = ("CONVERGED", "PLATEAU", "CAP")
TOP_SCORE = 5
# PLATEAU when the best paired gain stays below the noise this many rounds running.
PLATEAU_ROUNDS = 2
# A paired gain at or above this on one dimension is reported for a human read.
SUSPECT_JUMP = 2
# The decision that replaces CONVERGED or SEALED when the candidate addresses a judge.
BLOCKED = "BLOCKED BY FLAG"
# The spec key that acknowledges the injection flags, frozen and hashed with the rubric.
INJECTION_ACK = "injection_acknowledged"


def measured_ok(spec: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Whether every measured criterion passed in this round.

    Args:
        spec: The frozen spec.
        entry: The round's ledger entry.
    """
    if not spec.get("measured"):
        return True
    got = {m["id"]: m["pass"] for m in entry.get("measured", [])}
    return all(got.get(m["id"]) is True for m in spec["measured"])


def all_top(entry: dict[str, Any]) -> bool:
    """Whether every median of the round is the top score.

    Args:
        entry: The round's ledger entry.
    """
    return all(
        e["median"] == TOP_SCORE
        for p in entry["personas"].values()
        for e in p["dimensions"].values()
    )


def sealed_done(d: dict[str, Any]) -> bool:
    """Whether a decision follows a sealed panel, its grade standing or blocked."""
    return d["decision"] == "SEALED" or d.get("blocked") == "SEALED"


def flagged_roles(entry: dict[str, Any]) -> dict[str, list[str]]:
    """The injection flags of a round, by the role of the item that matched.

    Every text placed in a brief is scanned: the candidate, the control, the previous version,
    the round-1 baseline and the known-good. A round briefed before 0.3.1 holds the candidate's
    flags only.

    Args:
        entry: The round's ledger entry.

    Returns:
        The patterns that matched, by role; empty when no item addresses a judge.
    """
    by_role = entry.get("injection_flags_by_role")
    if isinstance(by_role, dict):
        return {role: list(found) for role, found in by_role.items() if found}
    flags = list(entry.get("injection_flags") or [])
    return {"candidate": flags} if flags else {}


def _gate(spec: dict[str, Any], entry: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """CONVERGED or SEALED on a round whose brief carried text addressed to a judge, in any
    item, becomes BLOCKED BY FLAG, unless the spec acknowledged the flags at init."""
    by_role = flagged_roles(entry)
    flags = sorted({p for found in by_role.values() for p in found})
    if not flags or spec.get(INJECTION_ACK):
        return d
    if d["decision"] == "SEALED":
        step = "report; the grade does not stand: grade the text without it in a new run"
    elif entry["round"] < spec["max_rounds"]:
        step = f"remove the text addressed to a judge, then brief round {entry['round'] + 1}"
    else:
        step = "the round limit is reached: start a new run without the text addressed to a judge"
    return {
        **d,
        "decision": BLOCKED,
        "blocked": d["decision"],
        "flags": flags,
        "roles": sorted(by_role),
        "why": f"text addressed to a judge in the {', '.join(sorted(by_role))} item, and the "
        f"spec does not acknowledge it ({INJECTION_ACK})",
        "next": step,
    }


# What a blocked run whose sealed panel can never stand is told to do.
NEW_RUN = (
    "start a new run without the text addressed to a judge, or acknowledge it in that run's "
    f"spec with `sealed-panel init` at a terminal ({INJECTION_ACK})"
)


def _baseline_gate(
    spec: dict[str, Any], loop: list[dict[str, Any]], d: dict[str, Any]
) -> dict[str, Any]:
    """A stop decision says "seal" only when a sealed panel could stand. The sealed panel
    scores the round-1 text as its baseline, so when that text addressed a judge and the spec
    does not acknowledge it, no sealed grade of this run can stand: the decision is BLOCKED BY
    FLAG on `baseline`, and the next step is a new run, before a sealed panel is spent."""
    first = next((r for r in loop if r["round"] == 1), {})
    flags = flagged_roles(first).get("candidate")
    if not flags or spec.get(INJECTION_ACK) or d["decision"] not in STOP_DECISIONS:
        return d
    return {
        **d,
        "decision": BLOCKED,
        "blocked": d["decision"],
        "flags": sorted(flags),
        "roles": ["baseline"],
        "why": "the round-1 text addressed a judge, and the sealed panel would score it as its "
        f"baseline; the spec does not acknowledge it ({INJECTION_ACK})",
        "next": NEW_RUN,
    }


def decide(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    """The stop rule: what the ledger says comes next.

    Args:
        spec: The frozen spec.
        ledger: The run's ledger: the aggregated rounds.
    """
    n = PLATEAU_ROUNDS
    rounds = ledger["rounds"]
    sealed = [r for r in rounds if r.get("sealed")]
    if sealed:
        done = {"decision": "SEALED", "final": sealed[-1], "panels": len(sealed)}
        return _gate(spec, sealed[-1], done)
    loop = [r for r in rounds if not r.get("sealed")]
    if not loop:
        return {"decision": "CONTINUE", "next_round": 1, "why": "no round yet"}
    last = loop[-1]
    instruments_ok = measured_ok(spec, last)
    if all_top(last) and instruments_ok:
        converged = {"decision": "CONVERGED", "round": last["round"], "next": "seal"}
        return _baseline_gate(spec, loop, _gate(spec, last, converged))
    gains = [r["best_gain"] for r in loop if r["round"] >= 2 and r["best_gain"] is not None]
    if len(gains) >= n and all(g < last["noise"] for g in gains[-n:]):
        plateau = {
            "decision": "PLATEAU",
            "round": last["round"],
            "next": "seal",
            "why": f"best paired gain {gains[-n:]} below noise {last['noise']} {n} rounds running",
        }
        return _baseline_gate(spec, loop, plateau)
    if last["round"] >= spec["max_rounds"]:
        cap = {"decision": "CAP", "round": last["round"], "next": "seal"}
        return _baseline_gate(spec, loop, cap)
    why = [] if instruments_ok else ["a measured criterion has not passed this round"]
    return {"decision": "CONTINUE", "next_round": last["round"] + 1, "why": why}
