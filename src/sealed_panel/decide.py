"""The stop rule, read from the ledger: CONTINUE, CONVERGED, PLATEAU, CAP or SEALED."""

from __future__ import annotations

from typing import Any

STOP_DECISIONS = ("CONVERGED", "PLATEAU", "CAP")
TOP_SCORE = 5
# PLATEAU when the best paired gain stays below the noise this many rounds running.
PLATEAU_ROUNDS = 2
# A paired gain at or above this on one dimension is reported for a human read.
SUSPECT_JUMP = 2


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


def decide(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    n = PLATEAU_ROUNDS
    rounds = ledger["rounds"]
    sealed = [r for r in rounds if r.get("sealed")]
    if sealed:
        return {"decision": "SEALED", "final": sealed[-1], "panels": len(sealed)}
    loop = [r for r in rounds if not r.get("sealed")]
    if not loop:
        return {"decision": "CONTINUE", "next_round": 1, "why": "no round yet"}
    last = loop[-1]
    instruments_ok = measured_ok(spec, last)
    if all_top(last) and instruments_ok:
        return {"decision": "CONVERGED", "round": last["round"], "next": "seal"}
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
