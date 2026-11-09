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
    if not spec.get("measured"):
        return True
    got = {m["id"]: m["pass"] for m in entry.get("measured", [])}
    return all(got.get(m["id"]) is True for m in spec["measured"])


def all_top(entry: dict[str, Any]) -> bool:
    return all(
        e["median"] == TOP_SCORE
        for p in entry["personas"].values()
        for e in p["dimensions"].values()
    )


def sealed_done(d: dict[str, Any]) -> bool:
    """Whether a decision follows a sealed panel, its grade standing or blocked."""
    return d["decision"] == "SEALED" or d.get("blocked") == "SEALED"


def _gate(spec: dict[str, Any], entry: dict[str, Any], d: dict[str, Any]) -> dict[str, Any]:
    """CONVERGED or SEALED on a candidate that addresses a judge becomes BLOCKED BY FLAG,
    unless the spec acknowledged the flags at init."""
    flags = list(entry.get("injection_flags") or [])
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
        "why": "the candidate contains text addressed to a judge, and the spec does not "
        f"acknowledge it ({INJECTION_ACK})",
        "next": step,
    }


def decide(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
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
        return _gate(spec, last, converged)
    gains = [r["best_gain"] for r in loop if r["round"] >= 2 and r["best_gain"] is not None]
    if len(gains) >= n and all(g < last["noise"] for g in gains[-n:]):
        return {
            "decision": "PLATEAU",
            "round": last["round"],
            "next": "seal",
            "why": f"best paired gain {gains[-n:]} below noise {last['noise']} {n} rounds running",
        }
    if last["round"] >= spec["max_rounds"]:
        return {"decision": "CAP", "round": last["round"], "next": "seal"}
    why = [] if instruments_ok else ["a measured criterion has not passed this round"]
    return {"decision": "CONTINUE", "next_round": last["round"] + 1, "why": why}
