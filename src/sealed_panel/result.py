"""The result file (`grade.json`): the sealed grade, how it was obtained and who judged, in one
versioned, machine-readable file.

`build` derives it from the run's files: the frozen spec and configuration, the aggregated
rounds and the sealed round's attempts. The sealed aggregate step writes it (`write_result`),
and the report prints its sealed grade from the same `build`, so the two cannot disagree.
`GradeResult` is the file as `grade()` returns it, and `GradeResult.read` reads one back.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from .decide import TOP_SCORE, decide, flagged_roles, measured_ok
from .errors import EvalError
from .ledger import results_by_judge
from .ledger import view as ledger_view
from .model import (
    INJECTION_ACK,
    RESULT_FILE,
    RESULT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    Finding,
    FrozenSpec,
    LedgerView,
    ResultAttempts,
    ResultCell,
    ResultFile,
    ResultFlags,
    ResultJudge,
    ResultObtained,
    RoundResult,
    TemplateRef,
)
from .run import load_frozen, run_config
from .settings import NOT_GRADING, Config, default_data
from .store import dump_json, load_json, read_text, round_dir

# The distribution whose release writes the result file.
DISTRIBUTION = "sealed-panel"
# The decision of a sealed panel its judges left without a grade: nothing was aggregated.
INCOMPLETE = "INCOMPLETE"
# Values longer than this are shown as their size and hash in the settings list.
SHOW_LIMIT = 80
# Settings always shown in full: what runs as the judge is never reduced to a hash.
SHOW_IN_FULL = frozenset({"judge.command", "judge.env_allow"})


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


def _show(value: Any, where: str, key: str = "") -> str:
    """A value in full, or, when it is long, its size and short hash and where to read it."""
    text = json.dumps(value, ensure_ascii=True)
    if len(text) <= SHOW_LIMIT or key in SHOW_IN_FULL:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"<{len(text)} characters, sha256 {digest}, see {where}>"


def overrides(cfg: Config, spec: Mapping[str, Any] | None = None) -> list[str]:
    """Every setting that differs from the packaged defaults, as `key = value (default x)`.

    Per-run keys are read from the frozen spec when one is given, since that is the value
    the run uses; the other keys come from the configuration.

    Args:
        cfg: The configuration.
        spec: The frozen spec, or None.
    """
    defaults = _flatten(default_data())
    current = _flatten(cfg.data)
    out = []
    for key in sorted(set(defaults) | set(current)):
        if key.startswith("run.") and spec is not None:
            continue
        if key in NOT_GRADING:
            continue  # where files are, not how a text is graded
        if current.get(key) != defaults.get(key):
            now = _show(current.get(key), "config.json", key)
            was = _show(defaults.get(key), "defaults", key)
            out.append(f"{key} = {now} (default {was})")
    if spec is not None:
        for key, default in default_data()["run"].items():
            if key in spec and spec[key] != default:
                now, was = _show(spec[key], "spec.json"), _show(default, "defaults")
                out.append(f"spec.{key} = {now} (default {was})")
    return out


def _env_union(rounds: list[RoundResult]) -> dict[str, set[str]] | None:
    found: dict[str, set[str]] | None = None
    for r in rounds:
        hashes = r.get("runner_env_hashes")
        if isinstance(hashes, dict):
            found = found if found is not None else {}
            for name, values in hashes.items():
                found.setdefault(name, set()).update(values)
    return found


def panel_env_changes(ledger: LedgerView) -> list[str]:
    """The `[judge] env_allow` names whose values differ between the sealed panel and the loop
    rounds, by their salted hashes; empty when either side carries no hashes.

    Args:
        ledger: The run's ledger.
    """
    sealed = _env_union([r for r in ledger["rounds"] if r.get("sealed")])
    loop = _env_union([r for r in ledger["rounds"] if not r.get("sealed")])
    if sealed is None or loop is None:
        return []
    return sorted(n for n in set(sealed) | set(loop) if sealed.get(n) != loop.get(n))


def release() -> str:
    """The installed release of sealed-panel, read from its distribution's metadata (the
    package's root imports every layer, so this module cannot import `__version__`), or
    "unknown" when the package runs from a folder without being installed."""
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        return "unknown"


def verdict(spec: FrozenSpec, final: RoundResult) -> str:
    """What the sealed grade says, in the report's words.

    Args:
        spec: The frozen spec.
        final: The sealed round's result.
    """
    lows = [
        f"{pid}/{did}"
        for pid, p in final["personas"].items()
        for did, e in p["dimensions"].items()
        if e["median"] < TOP_SCORE
    ]
    if not lows and measured_ok(spec, final):
        return f"converged: {TOP_SCORE} on every dimension"
    if lows:
        return f"below {TOP_SCORE} on " + ", ".join(lows)
    return "a measured criterion failed or was not measured"


def _grade(final: RoundResult) -> dict[str, dict[str, ResultCell]]:
    return {
        pid: {
            did: {
                "median": e["median"],
                "scores": e["scores"],
                "severities": e["severities"],
                "sd": e["sd"],
                "baseline_median": e.get("baseline_median"),
            }
            for did, e in p["dimensions"].items()
        }
        for pid, p in final["personas"].items()
    }


def _judge(cfg: Config) -> ResultJudge:
    argv = [str(arg) for arg in cfg.judge["command"]]
    return {
        "mode": "runner" if argv else "host",
        "argv": argv,
        "model_id": str(cfg.judge["model_id"]),
        "transcript_format": None if argv else cfg.transcript_format,
    }


def _prompt_extras(run: Path, final: RoundResult) -> dict[str, int]:
    """What each seat's counted judge carried in its prompt beyond its brief, as its format's
    adapter counted it: every valid attempt read from a transcript, a zero included."""
    rdir = round_dir(run, final["round"], True)
    return {
        str(judge): r.get("prompt_extra_chars", 0)
        for judge, attempts in sorted(results_by_judge(rdir).items())
        for r in attempts
        if r["ok"] and r.get("transcript")
    }


def _flags(run: Path, spec: FrozenSpec, final: RoundResult, ledger: LedgerView) -> ResultFlags:
    return {
        "injection": flagged_roles(final),
        "injection_acknowledged": spec.get(INJECTION_ACK),
        "pasted_replies": "output" in final["sources"],
        "judge_env_changed": final["runner_env_changed"],
        "panel_env_changed": panel_env_changes(ledger),
        "prompt_extras": _prompt_extras(run, final),
        "one_quote_judges": final["one_quote_judges"],
    }


def _obtained(spec: FrozenSpec, final: RoundResult, ledger: LedgerView) -> ResultObtained:
    loop = [r for r in ledger["rounds"] if not r.get("sealed")]
    stop = decide(spec, {**ledger, "rounds": loop})["decision"] if loop else None
    return {
        "loop_rounds": len(loop),
        "loop_decision": stop,
        "seats": spec["judges_per_persona"] * len(spec["personas"]),
        "composite": final["composite"],
        "noise": final["noise"],
        "measured": final.get("measured", []),
    }


def _attempts(final: RoundResult) -> ResultAttempts:
    return {
        "valid": sum(len(p["judges"]) for p in final["personas"].values()),
        "rejected": len(final["rejected_judges"]),
        "void": len(final["voided"]),
        "sources": final["sources"],
    }


def build(run: Path, spec: FrozenSpec, cfg: Config, ledger: LedgerView) -> ResultFile | None:
    """The result file's content, derived from the run's files; None before a sealed panel is
    aggregated. The report prints its sealed grade from it, and the sealed aggregate step
    writes it.

    Args:
        run: The run directory.
        spec: The run's frozen spec.
        cfg: The run's frozen configuration.
        ledger: The run's ledger.
    """
    sealed = [r for r in ledger["rounds"] if r.get("sealed")]
    if not sealed:
        return None
    final = sealed[-1]
    d = decide(spec, ledger)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "sealed_panel": release(),
        "run_schema_version": SCHEMA_VERSION,
        "title": spec["title"],
        "rubric_sha256": ledger["rubric_sha256"],
        "config_sha256": read_text(run / "config.lock").strip(),
        "template": spec.get("template"),
        "candidate_hash": final["candidate_hash"],
        "decision": d["decision"],
        "stands": d["decision"] == "SEALED",
        "why": str(d.get("why") or ""),
        "verdict": verdict(spec, final),
        "grade": _grade(final),
        "obtained": _obtained(spec, final, ledger),
        "findings": {pid: p["findings"] for pid, p in final["personas"].items()},
        "judge": _judge(cfg),
        "flags": _flags(run, spec, final, ledger),
        "attempts": _attempts(final),
        "settings": overrides(cfg, spec),
    }


def result_view(run: Path) -> ResultFile | None:
    """The run's result file content, derived from its files; None before a sealed panel is
    aggregated.

    Args:
        run: The run directory.
    """
    return build(run, load_frozen(run), run_config(run), ledger_view(run))


def write_result(run: Path) -> Path:
    """Write the result file from the run's files and return its path. The sealed aggregate
    step calls it once the sealed round's result is stored: written any earlier, it would hold
    no sealed panel, and it is refused.

    Args:
        run: The run directory.
    """
    view = result_view(run)
    if view is None:
        raise EvalError("no sealed panel is aggregated in this run: there is no result to write")
    path = run / RESULT_FILE
    dump_json(path, view)
    return path


@dataclass(frozen=True)
class GradeResult:
    """A sealed grade as `grade()` returns it: the result file's fields, the run and the file.

    `stands` is True only for a sealed grade that stands. A sealed panel blocked by text
    addressed to a judge has `decision` BLOCKED BY FLAG: its figures are there, and they do not
    stand. A sealed panel its judges left without a grade has `decision` INCOMPLETE, no result
    file (`path` None) and no figures; `why` says what is missing, and the run stays open for a
    replacement judge. The fields after `why` are the file's keys (docs/api.md has the table).

    Args:
        run: The run directory.
        path: The result file, or None when nothing was aggregated.
        decision: SEALED, BLOCKED BY FLAG or INCOMPLETE.
        stands: Whether the grade stands.
        why: Why the grade does not stand, or empty.
        schema_version: The result file's format.
        sealed_panel: The release that wrote the file.
        run_schema_version: The run's format.
        title: The spec's title.
        rubric_sha256: The rubric hash frozen at init.
        config_sha256: The configuration's fingerprint frozen at init.
        template: The template the spec came from, or None.
        candidate_hash: The sha256 of the graded text, normalised.
        verdict: What the sealed grade says, in the report's words.
        grade: Per persona and dimension: the median and its scores.
        obtained: The loop before the sealed panel, and the panel's figures.
        findings: Per persona, the blocking and material reasons the judges gave.
        judge: Who judged: the judge command, or the host and its transcript format.
        flags: What the report flags next to the grade.
        attempts: The sealed panel's valid, rejected and void attempts.
        settings: Every setting that differs from the defaults.
    """

    run: Path
    path: Path | None
    decision: str
    stands: bool
    why: str
    schema_version: int = RESULT_SCHEMA_VERSION
    sealed_panel: str = ""
    run_schema_version: int = SCHEMA_VERSION
    title: str = ""
    rubric_sha256: str = ""
    config_sha256: str = ""
    template: TemplateRef | None = None
    candidate_hash: str | None = None
    verdict: str = ""
    grade: dict[str, dict[str, ResultCell]] = field(default_factory=dict)
    obtained: ResultObtained | None = None
    findings: dict[str, list[Finding]] = field(default_factory=dict)
    judge: ResultJudge | None = None
    flags: ResultFlags | None = None
    attempts: ResultAttempts | None = None
    settings: list[str] = field(default_factory=list)

    @classmethod
    def read(cls, path: Path) -> GradeResult:
        """Read a result file. Every result format up to this release's is read; a later one
        is refused with the advice to upgrade, and a file without the keys of its format is
        refused as damaged.

        Args:
            path: The result file, `RUN/grade.json`.
        """
        data = load_json(path)
        version = data.get("schema_version") if isinstance(data, dict) else None
        if type(version) is not int or version < 1:
            raise EvalError(f"{path} is not a sealed-panel result file: no schema_version")
        if version > RESULT_SCHEMA_VERSION:
            raise EvalError(
                f"{path} is a result file of format {version}, written by a newer release; "
                f"this release reads formats up to {RESULT_SCHEMA_VERSION}: upgrade sealed-panel "
                "to read it"
            )
        expected = set(ResultFile.__required_keys__)
        if set(data) != expected:
            raise EvalError(
                f"{path} is damaged: a result file of format {version} holds the keys "
                f"{sorted(expected)}, this one {sorted(data)}"
            )
        return cls(run=path.parent, path=path, **data)

    @classmethod
    def incomplete(cls, run: Path, why: str) -> GradeResult:
        """A sealed panel its judges left without a grade: no file, no figures, and why.

        Args:
            run: The run directory.
            why: What the aggregate step found missing.
        """
        return cls(run=run, path=None, decision=INCOMPLETE, stands=False, why=why)
