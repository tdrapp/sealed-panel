"""Aggregate one round: medians per persona and dimension, paired gains, noise, findings."""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from .config import Config
from .decide import SUSPECT_JUMP
from .errors import EvalError
from .ledger import results_by_judge
from .record import round_keys, transcript_agent_id
from .report import write_feedback
from .spec import dims_for, load_frozen, run_config
from .store import dump_json, load_json, round_dir

# Files larger than this are skipped when looking for briefed but unrecorded agents.
MAX_SCAN_BYTES = 20_000_000


def unrecorded_agents(
    keys: dict[str, Any], by_judge: dict[int, list[dict[str, Any]]], cfg: Config
) -> list[tuple[int, str]]:
    """Agents whose transcript (in a folder a recorded transcript came from) holds one of this
    round's brief ids but who were never recorded: launching several and keeping the best."""
    nonces = {j.get("nonce"): j["judge"] for j in keys["judges"] if j.get("nonce")}
    recorded = {r["agent_id"] for rs in by_judge.values() for r in rs}
    folders = {
        Path(r["transcript"]).parent for rs in by_judge.values() for r in rs if r.get("transcript")
    }
    found = []
    for folder in folders:
        if not folder.is_dir():
            continue  # a purged temporary folder: nothing left to compare
        for f in folder.iterdir():
            if not f.is_file() or f.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for nonce, judge in nonces.items():
                if nonce in text:
                    aid = transcript_agent_id(f, cfg) or f.stem
                    if aid not in recorded:
                        found.append((judge, aid))
    return sorted(set(found))


def _dimension_entry(pv: list[dict[str, Any]], pid: str, did: str, cfg: Config) -> dict[str, Any]:
    cand = [
        r["items"]["candidate"]["scores"][did]
        for r in pv
        if did in r["items"].get("candidate", {}).get("scores", {})
    ]
    if len(cand) < cfg.min_valid_judges:
        raise EvalError(
            f"persona {pid}/{did}: fewer than {cfg.min_valid_judges} verified candidate scores"
        )
    sevs = [
        r["items"]["candidate"]["severity"][did]
        for r in pv
        if did in r["items"].get("candidate", {}).get("severity", {})
    ]
    entry: dict[str, Any] = {
        "median": statistics.median(cand),
        "scores": cand,
        "severities": sevs,
        "sd": round(statistics.pstdev(cand), 3),
    }
    gains = []
    for r in pv:
        c = r["items"].get("candidate", {}).get("scores", {}).get(did)
        p = r["items"].get("previous", {}).get("scores", {}).get(did)
        if c is not None and p is not None:
            gains.append(c - p)
    if gains:
        entry["paired_gain"] = statistics.median(gains)
    baseline = [r["items"].get("baseline", {}).get("scores", {}).get(did) for r in pv]
    baseline = [x for x in baseline if x is not None]
    if baseline:
        entry["baseline_median"] = statistics.median(baseline)
    return entry


def _findings(pv: list[dict[str, Any]], cfg: Config) -> list[dict[str, Any]]:
    findings = []
    for r in pv:
        for did, sev in r["items"].get("candidate", {}).get("severity", {}).items():
            if sev in cfg.findings:
                wp = r["items"]["candidate"]["weakest_point"]
                text = wp["text"] if wp.get("dimension") == did else ""
                reason = r["items"]["candidate"]["reasoning"].get(did, "")
                findings.append(
                    {"judge": r["judge"], "dimension": did, "severity": sev, "text": text or reason}
                )
    return findings


def _check_complete(
    spec: dict[str, Any],
    rdir: Path,
    keys: dict[str, Any],
    by_judge: dict[int, list[dict[str, Any]]],
    sealed: bool,
    cfg: Config,
) -> None:
    if sealed and spec.get("measured"):
        path = rdir / "measured.json"
        done = {m["id"] for m in (load_json(path) if path.exists() else [])}
        missing = [m["id"] for m in spec["measured"] if m["id"] not in done]
        if missing:
            raise EvalError(
                f"measure {missing} before aggregating the sealed panel: it happens once"
            )
    silent = [
        j["judge"]
        for j in keys["judges"]
        if not any(not r.get("void") for r in by_judge.get(j["judge"], []))
    ]
    if silent:
        raise EvalError(
            f"judge slot(s) {silent} have no real attempt: a void clears a block but never "
            "fills a seat; brief a fresh replacement on the same brief (stopping after two "
            "replies is a re-roll)"
        )
    unrecorded = unrecorded_agents(keys, by_judge, cfg)
    if unrecorded:
        raise EvalError(
            "agents received a brief of this round but were never recorded: "
            + ", ".join(f"j{j} {aid}" for j, aid in unrecorded)
            + "; record every agent you briefed, rejected or not (--void for one that never "
            "replied)"
        )


def aggregate_round(run: Path, rnd: int, sealed: bool) -> dict[str, Any]:
    """Aggregate a round once, append it to the ledger and return the ledger entry. After a
    loop round, also write the author's findings to RUN/feedback/after_rN.md."""
    spec = load_frozen(run)
    cfg = run_config(run)
    rdir = round_dir(run, rnd, sealed)
    if (rdir / "result.json").exists():
        raise EvalError("this round is already aggregated: a round is aggregated once")
    keys = round_keys(rdir, rnd)
    by_judge = results_by_judge(rdir)
    _check_complete(spec, rdir, keys, by_judge, sealed, cfg)
    valid = [next(r for r in rs if r["ok"]) for rs in by_judge.values() if any(r["ok"] for r in rs)]
    rejected = [r for rs in by_judge.values() for r in rs if not r["ok"]]
    per_persona: dict[str, dict[str, Any]] = {}
    for persona in spec["personas"]:
        pid = persona["id"]
        pv = [r for r in valid if r["persona"] == pid]
        if len(pv) < cfg.min_valid_judges:
            why = [(r["judge"], r["errors"][:1]) for r in rejected if r["persona"] == pid]
            raise EvalError(
                f"persona {pid}: {len(pv)} valid judge(s) in this round, "
                f"{cfg.min_valid_judges} needed (rejected: {why})"
            )
        dims = {d["id"]: _dimension_entry(pv, pid, d["id"], cfg) for d in dims_for(spec, pid)}
        per_persona[pid] = {
            "judges": [r["judge"] for r in pv],
            "dimensions": dims,
            "findings": _findings(pv, cfg),
            "control_means": [r.get("control_mean") for r in pv],
        }
    all_sd = [e["sd"] for p in per_persona.values() for e in p["dimensions"].values()]
    noise = max(spec["noise_floor_min"], round(statistics.mean(all_sd), 3))
    gains = [
        e["paired_gain"]
        for p in per_persona.values()
        for e in p["dimensions"].values()
        if "paired_gain" in e
    ]
    weights = {d["id"]: d.get("weight", 1) for d in spec["dimensions"]}
    pairs = [(did, e) for p in per_persona.values() for did, e in p["dimensions"].items()]
    composite = sum(weights[did] * e["median"] for did, e in pairs) / sum(
        weights[did] for did, _ in pairs
    )
    entry: dict[str, Any] = {
        "round": rnd,
        "sealed": sealed,
        "candidate_hash": keys.get("candidate_hash"),
        "personas": per_persona,
        "composite": round(composite, 2),
        "noise": noise,
        "best_gain": max(gains) if gains else None,
        "suspect_jumps": [
            f"{pid}/{did}"
            for pid, p in per_persona.items()
            for did, e in p["dimensions"].items()
            if e.get("paired_gain", 0) >= SUSPECT_JUMP
        ],
        "rejected_judges": [
            {"judge": r["judge"], "errors": r["errors"]} for r in rejected if not r.get("void")
        ],
        "voided": [
            {"judge": r["judge"], "agent_id": r["agent_id"], "replied": bool(r.get("replied"))}
            for r in rejected
            if r.get("void")
        ],
        "sources": sorted({r.get("source", "output") for r in valid}),
        "injection_flags": keys.get("injection_flags", []),
        "prompt_extras": {
            str(r["judge"]): r["prompt_extra_chars"] for r in valid if r.get("prompt_extra_chars")
        },
        "one_quote_judges": sorted(
            r["judge"] for r in valid if r["items"].get("candidate", {}).get("one_quote")
        ),
    }
    if (rdir / "measured.json").exists():
        entry["measured"] = load_json(rdir / "measured.json")
    dump_json(rdir / "result.json", entry)
    ledger = load_json(run / "ledger.json")
    ledger["rounds"] = [
        r for r in ledger["rounds"] if not (r["round"] == rnd and r.get("sealed") == sealed)
    ]
    ledger["rounds"].append(entry)
    ledger["rounds"].sort(key=lambda r: (r.get("sealed", False), r["round"]))
    dump_json(run / "ledger.json", ledger)
    if not sealed:
        write_feedback(run, entry)
    return entry
