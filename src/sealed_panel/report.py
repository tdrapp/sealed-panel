"""What people read: the author's findings, the run status, and the final report."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .decide import (
    BLOCKED,
    SUSPECT_JUMP,
    TOP_SCORE,
    decide,
    flagged_roles,
    measured_ok,
    sealed_done,
)
from .ledger import other_runs, used_agent_ids
from .ledger import view as ledger_view
from .model import INJECTION_ACK
from .record import RUNNER
from .rubric import dims_for
from .run import load_frozen, run_config
from .settings import NOT_GRADING, Config, default_data
from .store import write_file

# Hex characters of the rubric hash shown in the report.
HASH_CHARS = 12
INJECTION_WARNING = (
    "WARNING: the {roles} item contains text addressed to a judge; remove it, or brief with "
    "another control or known-good."
)
FEEDBACK_CLOSING = (
    "One idea per round. Remove what does not serve the purpose (de-fluff).",
    "Cosmetic points are not reasons for a round. Do not write the rubric into the artefact.",
)


def feedback_for_author(entry: dict[str, Any]) -> str:
    """What the author reads: the judges' blocking and material reasons, not their numbers.
    Hygiene, not a guarantee: the ledger is readable; the protection is the fresh and sealed
    panels."""
    lines = [f"# Findings after round {entry['round']}", ""]
    for pid, p in entry["personas"].items():
        lines.append(f"## Persona {pid}")
        for f in p["findings"]:
            lines.append(f"- [{f['severity']}] ({f['dimension']}) {f['text']}")
        lines.append("")
    for m in entry.get("measured", []):
        if not m["pass"]:
            lines.append(f"- measured {m['id']} failed: {m['value']}")
    roles = sorted(flagged_roles(entry))
    if roles:
        lines.append(INJECTION_WARNING.format(roles=", ".join(roles)))
    lines += ["", *FEEDBACK_CLOSING]
    return "\n".join(lines) + "\n"


def feedback_path(run: Path, rnd: int) -> Path:
    """Where the author's findings after a loop round are written.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
    """
    return run / "feedback" / f"after_r{rnd}.md"


def write_feedback(run: Path, entry: dict[str, Any]) -> Path:
    """Write the author's findings for a loop round and return their path.

    Args:
        run: The run directory.
        entry: The round's ledger entry.
    """
    path = feedback_path(run, entry["round"])
    write_file(path, feedback_for_author(entry))
    return path


def status(run: Path) -> dict[str, Any]:
    """The decision for the run, read-only. On CONTINUE, names the author's findings file.

    Args:
        run: The run directory.
    """
    spec = load_frozen(run)
    cfg = run_config(run)
    ledger = ledger_view(run)
    d = decide(spec, ledger)
    loop = [r for r in ledger["rounds"] if not r.get("sealed")]
    if d["decision"] == "CONTINUE" and loop:
        fb = feedback_path(run, loop[-1]["round"])
        if fb.exists():
            d["feedback"] = str(fb)
    if d["decision"] == "SEALED":
        d = {"decision": "SEALED", "panels": d["panels"], "next": "report"}
    d.pop("final", None)
    if spec.get(INJECTION_ACK):
        d[INJECTION_ACK] = spec[INJECTION_ACK]
    d["overrides"] = overrides(cfg, spec)
    return d


def _cell(e: dict[str, Any]) -> str:
    return f"{e['median']:g} ({'/'.join(map(str, e['scores']))})"


def acknowledgement_line(spec: dict[str, Any]) -> str:
    """The injection acknowledgement, printed on the grade line when the spec has one."""
    ack = spec.get(INJECTION_ACK)
    return f" Injection flags acknowledged: {ack!r}." if ack else ""


def model_line(cfg: Config) -> str:
    """The judge model the run declared, printed next to the grade."""
    model_id = str(cfg.judge["model_id"])
    return f"Judge model: `{model_id}`." if model_id else "Judge model: not declared."


def _sealed_headline(spec: dict[str, Any], d: dict[str, Any], model: str) -> list[str]:
    f = d["final"]
    dims = [(pid, did, e) for pid, p in f["personas"].items() for did, e in p["dimensions"].items()]
    lows = [f"{pid}/{did}" for pid, did, e in dims if e["median"] < TOP_SCORE]
    grade = "; ".join(f"{did} {_cell(e)}" for _, did, e in dims)
    if not lows and measured_ok(spec, f):
        verdict = f"converged: {TOP_SCORE} on every dimension"
    elif lows:
        verdict = f"below {TOP_SCORE} on " + ", ".join(lows)
    else:
        verdict = "a measured criterion failed or was not measured"
    out = []
    if d["decision"] == BLOCKED:
        out += [
            f"**{BLOCKED}: the sealed panel's {', '.join(d['roles'])} item contains text "
            f"addressed to a judge ({', '.join(d['flags'])}); the grade below does not stand. "
            f"Grade the text without it in a new run, or acknowledge the flag in that run's "
            f"spec ({INJECTION_ACK}).**",
            "",
        ]
    out += [f"**Grade (sealed panel): {grade}. Verdict: {verdict}.** {model}", ""]
    moves = [
        f"{did} {e['baseline_median']:g} -> {e['median']:g}"
        for _, did, e in dims
        if "baseline_median" in e
    ]
    if moves:
        out += [
            "Round-1 text scored by the same sealed panel, baseline -> sealed: "
            + "; ".join(moves)
            + ".",
            "",
        ]
    return out


def _round_table(spec: dict[str, Any], ledger: dict[str, Any]) -> list[str]:
    cols = [(p["id"], dd["id"]) for p in spec["personas"] for dd in dims_for(spec, p["id"])]
    mids = [m["id"] for m in spec.get("measured") or []]
    hdr = [
        "round",
        *[f"{pid}/{did}" for pid, did in cols],
        *mids,
        "composite (info)",
        "best gain",
        "noise",
        "rejected",
    ]
    out = ["| " + " | ".join(hdr) + " |", "|" + "---|" * len(hdr)]
    for r in ledger["rounds"]:
        cells = [("sealed " if r.get("sealed") else "") + str(r["round"])]
        for pid, did in cols:
            cells.append(_cell(r["personas"][pid]["dimensions"][did]))
        got = {m["id"]: m for m in r.get("measured", [])}
        for mid in mids:
            m = got.get(mid)
            cells.append("" if m is None else f"{m['value']} {'pass' if m['pass'] else 'fail'}")
        bg = "" if r["best_gain"] is None else f"{r['best_gain']:+g}"
        cells += [f"{r['composite']:g}", bg, f"{r['noise']:g}", str(len(r["rejected_judges"]))]
        out.append("| " + " | ".join(cells) + " |")
    out += [
        "",
        "The composite is the weighted mean of the medians, for information only: the grade and "
        "the stop rule read the medians per dimension.",
        "",
    ]
    return out


def _notes(run: Path, ledger: dict[str, Any], d: dict[str, Any]) -> list[str]:
    out = []
    if sealed_done(d):
        f = d["final"]
        for pid, p in f["personas"].items():
            for x in p["findings"]:
                out.append(
                    f"- open finding, {pid} [{x['severity']}] ({x['dimension']}) {x['text']}"
                )
        loop = [r for r in ledger["rounds"] if not r.get("sealed")]
        same = [r for r in loop if r.get("candidate_hash") == f.get("candidate_hash")]
        if same:  # the same text judged by two fresh panels: measured repeat-test noise
            prev = same[-1]
            moves = [
                f"{did} {prev['personas'][pid]['dimensions'][did]['median']:g} -> {e['median']:g}"
                for pid, p in f["personas"].items()
                for did, e in p["dimensions"].items()
            ]
            out.append(
                f"- repeat-test on the same text (round {prev['round']} to sealed): "
                + ", ".join(moves)
            )
    others = other_runs(run)
    if others:
        out.append(
            f"- {len(others)} other run(s) beside this one ({', '.join(others)}): "
            "a new run is a fresh panel; say why each was started"
        )
    for r in ledger["rounds"]:
        name = ("sealed round " if r.get("sealed") else "round ") + str(r["round"])
        if r["suspect_jumps"]:
            out.append(
                f"- {name}: paired jump of {SUSPECT_JUMP} or more on "
                f"{', '.join(r['suspect_jumps'])}; verify by hand before believing it"
            )
        for role, found in sorted(flagged_roles(r).items()):
            out.append(f"- {name}: the {role} item addresses a judge {found}")
        for x in r.get("rejected_judges") or []:
            out.append(f"- {name}: judge {x['judge']} rejected: {'; '.join(x['errors'][:3])}")
        for v in r.get("voided") or []:
            if v.get("source") == RUNNER:
                why = f"by the judge runner: {v.get('reason')}"
                why += "; it wrote output, kept unread" if v.get("replied") else ""
            elif v.get("replied"):
                why = "after replying (brief not verbatim)"
            else:
                why = "(no reply)"
            out.append(f"- {name}: judge {v['judge']} attempt {v['agent_id']} voided {why}")
        for j, n in sorted((r.get("prompt_extras") or {}).items()):
            out.append(
                f"- {name}: judge {j}'s prompt carried {n} characters beyond its brief; "
                "read what was added"
            )
        for j in r.get("one_quote_judges") or []:
            out.append(
                f"- {name}: judge {j} quoted the same words for every dimension of the "
                "candidate; its scores may not rest on distinct evidence"
            )
        out += _source_notes(name, r)
    return out + _panel_env_note(ledger)


def _panel_env_note(ledger: dict[str, Any]) -> list[str]:
    changed = panel_env_changes(ledger)
    if not changed:
        return []
    return [
        f"- sealed panel: the judge command ran under different values of {', '.join(changed)} "
        "than in the loop rounds (another model or key): the loop's scores and the sealed grade "
        "come from different judges"
    ]


def _env_union(rounds: list[dict[str, Any]]) -> dict[str, set[str]] | None:
    found: dict[str, set[str]] | None = None
    for r in rounds:
        hashes = r.get("runner_env_hashes")
        if isinstance(hashes, dict):
            found = found if found is not None else {}
            for name, values in hashes.items():
                found.setdefault(name, set()).update(values)
    return found


def panel_env_changes(ledger: dict[str, Any]) -> list[str]:
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


def _source_notes(name: str, r: dict[str, Any]) -> list[str]:
    """What the report says about where a round's replies came from."""
    out = []
    if "output" in r.get("sources", []):
        out.append(f"- {name}: some replies were pasted, not read from a transcript")
    if RUNNER in r.get("sources", []):
        out.append(
            f"- {name}: some replies were read by sealed-panel from the judge command "
            "(judge.command in the settings above)"
        )
    changed = r.get("runner_env_changed") or []
    if changed:
        out.append(
            f"- {name}: the judge command ran under different values of {', '.join(changed)} "
            "across attempts (another model or key between judges): the declared judge model "
            "does not cover every attempt; put the model in judge.command to freeze it"
        )
    return out


def render_report(run: Path) -> str:
    """The run's report in markdown: the sealed grade, the round table and the notes.

    Args:
        run: The run directory.
    """
    spec = load_frozen(run)
    cfg = run_config(run)
    ledger = ledger_view(run)
    d = decide(spec, ledger)
    n_ctx = len(used_agent_ids(run))
    changed = overrides(cfg, spec)
    ref = spec.get("template") or {}
    template = (
        f"template {ref.get('id')} v{ref.get('version')}"
        + (" (modified)" if ref.get("modified") else "")
        + ", "
        if ref
        else ""
    )
    settings = (
        "settings: all defaults"
        if not changed
        else "settings that differ from the defaults: " + "; ".join(changed)
    )
    title = f"# Evaluation: {spec['title']}"
    if d["decision"] == BLOCKED:
        title += f" ({BLOCKED})"
    ack = (
        f" Injection flags acknowledged in the spec: {spec[INJECTION_ACK]!r}."
        if spec.get(INJECTION_ACK)
        else ""
    )
    out = [
        title,
        "",
        f"Rubric sha256 `{ledger['rubric_sha256'][:HASH_CHARS]}`, "
        f"decision **{d['decision']}**, {n_ctx} judge contexts, {template}{settings}.{ack}",
        "",
    ]
    if sealed_done(d):
        out += _sealed_headline(spec, d, model_line(cfg) + acknowledgement_line(spec))
    out += _round_table(spec, ledger)
    out += _notes(run, ledger, d)
    return "\n".join(out) + "\n"


def outcome_line(r: dict[str, Any]) -> str:
    """What `record` and `judge` print for one recorded attempt."""
    if r.get("void"):
        return f"VOID judge {r['judge']}: " + (
            "; ".join(r["errors"][:1])
            if r.get("source") == RUNNER
            else "recorded as an attempt without a reply"
        )
    return (
        ("VALID" if r["ok"] else "REJECTED")
        + f" judge {r['judge']}"
        + ("" if r["ok"] else ": " + "; ".join(r["errors"][:3]))
    )


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


SHOW_LIMIT = 80


# Settings always shown in full: what runs as the judge is never reduced to a hash.
SHOW_IN_FULL = frozenset({"judge.command", "judge.env_allow"})


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
