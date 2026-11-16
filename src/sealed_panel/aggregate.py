"""Aggregate one round: medians per persona and dimension, paired gains, noise, findings."""

from __future__ import annotations

import statistics
from pathlib import Path

from .decide import SUSPECT_JUMP
from .errors import EvalError
from .hosts import adapter, read_transcript
from .model import (
    Attempt,
    DimensionResult,
    Finding,
    FrozenSpec,
    Measurement,
    PersonaResult,
    RoundKeys,
    RoundResult,
    no_item,
)
from .rubric import dims_for
from .settings import Config
from .store import load_json

# Files larger than this are skipped when looking for briefed but unrecorded agents.
MAX_SCAN_BYTES = 20_000_000
# Transcripts the scan for unrecorded agents reads at most; past it, aggregation is refused.
MAX_SIBLINGS = 1000
# Seconds before the round's briefs a transcript may have been last written and still be read:
# one written earlier cannot hold a brief id of this round.
BRIEF_SLACK_S = 60


def _scanned(proofs: set[Path], cfg: Config, since: float) -> list[Path]:
    """The transcripts beside the recorded ones that the scan reads, as the run's format lists
    them; refused past MAX_SIBLINGS, so the scan stays bounded and never stops short."""
    found = sorted(adapter(cfg).siblings(sorted(proofs), since))
    if len(found) > MAX_SIBLINGS:
        raise EvalError(
            f"{len(found)} transcripts were written beside the recorded ones since this round "
            f"was briefed, more than the {MAX_SIBLINGS} the scan for unrecorded judges reads: "
            "launch judges where the host writes few other transcripts"
        )
    return [f for f in found if f.is_file() and f.stat().st_size <= MAX_SCAN_BYTES]


def _agent_of(f: Path, cfg: Config) -> str:
    """The agent a transcript holding a brief id belongs to; one that cannot be read is
    refused, since it could be a judge launched and kept quiet."""
    try:
        return read_transcript(f, cfg).agent_id or f.stem
    except EvalError as exc:
        raise EvalError(
            f"{f} holds a brief id of this round but cannot be read ({exc}): every agent that "
            "received a brief must be recorded, and this one cannot be"
        ) from exc


def unrecorded_agents(
    keys: RoundKeys, by_judge: dict[int, list[Attempt]], cfg: Config, since: float = 0.0
) -> list[tuple[int, str]]:
    """Agents whose transcript (beside a recorded one, as the run's transcript format lists
    them, written since `since`) holds one of this round's brief ids but who were never
    recorded: launching several and keeping the best."""
    nonces = {j.get("nonce"): j["judge"] for j in keys["judges"] if j.get("nonce")}
    recorded = {r["agent_id"] for rs in by_judge.values() for r in rs}
    proofs = {Path(t) for rs in by_judge.values() for r in rs if (t := r.get("transcript"))}
    found = []
    for f in _scanned(proofs, cfg, since):
        text = f.read_text(encoding="utf-8", errors="replace")
        judges = [judge for nonce, judge in nonces.items() if nonce in text]
        if judges and (aid := _agent_of(f, cfg)) not in recorded:
            found += [(judge, aid) for judge in judges]
    return sorted(set(found))


def _dimension_entry(pv: list[Attempt], pid: str, did: str, cfg: Config) -> DimensionResult:
    cand = [
        r["items"]["candidate"]["scores"][did]
        for r in pv
        if did in r["items"].get("candidate", no_item()).get("scores", {})
    ]
    if len(cand) < cfg.min_valid_judges:
        raise EvalError(
            f"persona {pid}/{did}: fewer than {cfg.min_valid_judges} verified candidate scores"
        )
    sevs = [
        r["items"]["candidate"]["severity"][did]
        for r in pv
        if did in r["items"].get("candidate", no_item()).get("severity", {})
    ]
    entry: DimensionResult = {
        "median": statistics.median(cand),
        "scores": cand,
        "severities": sevs,
        "sd": round(statistics.pstdev(cand), 3),
    }
    gains = []
    for r in pv:
        c = r["items"].get("candidate", no_item()).get("scores", {}).get(did)
        p = r["items"].get("previous", no_item()).get("scores", {}).get(did)
        if c is not None and p is not None:
            gains.append(c - p)
    if gains:
        entry["paired_gain"] = statistics.median(gains)
    control = [
        x
        for r in pv
        if (x := r["items"].get("control", no_item()).get("raw_scores", {}).get(did)) is not None
    ]
    if control:
        entry["control_median"] = statistics.median(control)
    baseline = [
        x
        for r in pv
        if (x := r["items"].get("baseline", no_item()).get("scores", {}).get(did)) is not None
    ]
    if baseline:
        entry["baseline_median"] = statistics.median(baseline)
    return entry


def _findings(pv: list[Attempt], cfg: Config) -> list[Finding]:
    findings: list[Finding] = []
    for r in pv:
        for did, sev in r["items"].get("candidate", no_item()).get("severity", {}).items():
            if sev in cfg.findings:
                wp = r["items"]["candidate"]["weakest_point"]
                text = wp["text"] if wp.get("dimension") == did else ""
                reason = r["items"]["candidate"]["reasoning"].get(did, "")
                findings.append(
                    {"judge": r["judge"], "dimension": did, "severity": sev, "text": text or reason}
                )
    return findings


def _check_complete(
    spec: FrozenSpec,
    rdir: Path,
    keys: RoundKeys,
    by_judge: dict[int, list[Attempt]],
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
    briefed = (rdir / "keys.json").stat().st_mtime - BRIEF_SLACK_S
    unrecorded = unrecorded_agents(keys, by_judge, cfg, briefed)
    if unrecorded:
        raise EvalError(
            "agents received a brief of this round but were never recorded: "
            + ", ".join(f"j{j} {aid}" for j, aid in unrecorded)
            + "; record every agent you briefed, rejected or not (a void for one that never "
            "replied)"
        )


def env_changes(by_judge: dict[int, list[Attempt]]) -> list[str]:
    """The `[judge] env_allow` names whose value differed between the round's runner attempts.

    Every attempt counts, rejected and void ones included: a judge replaced after a rejection
    may have run another model. An attempt without hashes (host mode) is left out.

    Args:
        by_judge: The round's recorded attempts, per judge slot.

    Returns:
        The names, sorted; empty when every attempt ran under the same values.
    """
    seen = _hashed_attempts(by_judge)
    names = {n for hashes in seen for n in hashes}
    return sorted(n for n in names if len({hashes.get(n) for hashes in seen}) > 1)


def _hashed_attempts(by_judge: dict[int, list[Attempt]]) -> list[dict[str, str]]:
    return [
        hashes
        for attempts in by_judge.values()
        for r in attempts
        if isinstance(hashes := (r.get("runner") or {}).get("env_sha256"), dict)
    ]


def env_values(by_judge: dict[int, list[Attempt]]) -> dict[str, list[str]] | None:
    """Every salted hash each `[judge] env_allow` name had over the round's attempts.

    Args:
        by_judge: The round's recorded attempts, per judge slot.

    Returns:
        The hashes by name, sorted; None when no attempt carries hashes.
    """
    seen = _hashed_attempts(by_judge)
    if not seen:
        return None
    names = {n for hashes in seen for n in hashes}
    return {n: sorted({h[n] for h in seen if n in h}) for n in sorted(names)}


def aggregate(
    spec: FrozenSpec,
    cfg: Config,
    rdir: Path,
    keys: RoundKeys,
    by_judge: dict[int, list[Attempt]],
    rnd: int,
    sealed: bool,
) -> RoundResult:
    """A round's result: medians per persona and dimension, paired gains, noise, findings and
    flags, refused when the round is not complete. It writes nothing and takes no lock: the
    aggregate step (steps.aggregate_round) holds the seats and the run, and stores the result.

    Args:
        spec: The frozen spec.
        cfg: The run's configuration.
        rdir: The round directory, read for the instrument readings and the transcripts.
        keys: The round's keys.json.
        by_judge: The round's recorded attempts, per judge slot.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
    """
    _check_complete(spec, rdir, keys, by_judge, sealed, cfg)
    valid = [next(r for r in rs if r["ok"]) for rs in by_judge.values() if any(r["ok"] for r in rs)]
    rejected = [r for rs in by_judge.values() for r in rs if not r["ok"]]
    per_persona: dict[str, PersonaResult] = {}
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
    entry: RoundResult = {
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
            {
                "judge": r["judge"],
                "agent_id": r["agent_id"],
                "replied": bool(r.get("replied")),
                "source": r.get("source", "void"),
                "reason": (r.get("errors") or [""])[0],
            }
            for r in rejected
            if r.get("void")
        ],
        "sources": sorted({r.get("source", "output") for r in valid}),
        "injection_flags": keys.get("injection_flags", []),
        "injection_flags_by_role": keys.get("injection_flags_by_role"),
        "runner_env_changed": env_changes(by_judge),
        "runner_env_hashes": env_values(by_judge),
        "prompt_extras": {
            str(r["judge"]): r["prompt_extra_chars"] for r in valid if r.get("prompt_extra_chars")
        },
        "one_quote_judges": sorted(
            r["judge"] for r in valid if r["items"].get("candidate", no_item()).get("one_quote")
        ),
    }
    if (rdir / "measured.json").exists():
        measured: list[Measurement] = load_json(rdir / "measured.json")
        entry["measured"] = measured
    return entry
