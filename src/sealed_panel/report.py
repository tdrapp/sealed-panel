"""What people read: the author's findings, the run status, and the final report."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

from .decide import BLOCKED, SUSPECT_JUMP, decide, flagged_roles, sealed_done
from .ledger import other_runs, used_agent_ids
from .ledger import view as ledger_view
from .model import (
    INJECTION_ACK,
    RUNNER,
    Attempt,
    Decision,
    DimensionResult,
    FrozenSpec,
    LedgerView,
    RejectedJudge,
    ResultCell,
    ResultFile,
    RoundResult,
    Voided,
)
from .result import GradeResult, build, overrides
from .rubric import dims_for
from .run import load_frozen, run_config
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


def feedback_for_author(entry: RoundResult) -> str:
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


def write_feedback(run: Path, entry: RoundResult) -> Path:
    """Write the author's findings for a loop round and return their path.

    Args:
        run: The run directory.
        entry: The round's ledger entry.
    """
    path = feedback_path(run, entry["round"])
    write_file(path, feedback_for_author(entry))
    return path


def status(run: Path) -> Decision:
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


def _cell(e: DimensionResult | ResultCell) -> str:
    return f"{e['median']:g} ({'/'.join(map(str, e['scores']))})"


def acknowledgement_line(view: ResultFile) -> str:
    """The injection acknowledgement, printed on the grade line when the spec has one."""
    ack = view["flags"]["injection_acknowledged"]
    return f" Injection flags acknowledged: {ack!r}." if ack else ""


def model_line(view: ResultFile) -> str:
    """The judge model the run declared, printed next to the grade."""
    model_id = view["judge"]["model_id"]
    return f"Judge model: `{model_id}`." if model_id else "Judge model: not declared."


def _blocked_banner(view: ResultFile) -> list[str]:
    """The line above a sealed grade that does not stand, naming the flagged items."""
    if view["stands"]:
        return []
    by_role = view["flags"]["injection"]
    flags = sorted({p for found in by_role.values() for p in found})
    return [
        f"**{BLOCKED}: the sealed panel's {', '.join(sorted(by_role))} item contains text "
        f"addressed to a judge ({', '.join(flags)}); the grade below does not stand. "
        f"Grade the text without it in a new run, or acknowledge the flag in that run's "
        f"spec ({INJECTION_ACK}).**",
        "",
    ]


def _sealed_headline(view: ResultFile, model: str) -> list[str]:
    cells = [(did, e) for p in view["grade"].values() for did, e in p.items()]
    grade = "; ".join(f"{did} {_cell(e)}" for did, e in cells)
    out = _blocked_banner(view)
    out += [f"**Grade (sealed panel): {grade}. Verdict: {view['verdict']}.** {model}", ""]
    moves = [
        f"{did} {e['baseline_median']:g} -> {e['median']:g}"
        for did, e in cells
        if e["baseline_median"] is not None
    ]
    if moves:
        out += [
            "Round-1 text scored by the same sealed panel, baseline -> sealed: "
            + "; ".join(moves)
            + ".",
            "",
        ]
    return out


def _round_table(spec: FrozenSpec, ledger: LedgerView) -> list[str]:
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


def _open_findings(view: ResultFile | None) -> list[str]:
    """The sealed panel's blocking and material reasons, from the result view."""
    if view is None:
        return []
    return [
        f"- open finding, {pid} [{x['severity']}] ({x['dimension']}) {x['text']}"
        for pid, found in view["findings"].items()
        for x in found
    ]


class RoundNotes(NamedTuple):
    """What the report notes for one round: from its result for a loop round, from the result
    view for the sealed panel, so the report and the result file print the same values."""

    name: str
    suspect_jumps: list[str]
    injection: dict[str, list[str]]
    rejected: list[RejectedJudge]
    voided: list[Voided]
    prompt_extras: dict[str, int]
    one_quote: list[int]
    pasted: bool
    runner: bool
    env_changed: list[str]


def _from_round(r: RoundResult) -> RoundNotes:
    return RoundNotes(
        ("sealed round " if r.get("sealed") else "round ") + str(r["round"]),
        r["suspect_jumps"],
        flagged_roles(r),
        r.get("rejected_judges") or [],
        r.get("voided") or [],
        r.get("prompt_extras") or {},
        r.get("one_quote_judges") or [],
        "output" in r.get("sources", []),
        RUNNER in r.get("sources", []),
        r.get("runner_env_changed") or [],
    )


def _from_view(view: ResultFile, rnd: int) -> RoundNotes:
    """The sealed panel's notes, from the result view. A sealed brief has no previous version,
    so it has no paired gain and no suspect jump."""
    flags, attempts = view["flags"], view["attempts"]
    return RoundNotes(
        f"sealed round {rnd}",
        [],
        flags["injection"],
        attempts["rejected"],
        attempts["void"],
        flags["prompt_extras"],
        flags["one_quote_judges"],
        flags["pasted_replies"],
        RUNNER in attempts["sources"],
        flags["judge_env_changed"],
    )


def _void_line(name: str, v: Voided) -> str:
    if v.get("source") == RUNNER:
        why = f"by the judge runner: {v.get('reason')}"
        why += "; it wrote output, kept unread" if v.get("replied") else ""
    elif v.get("replied"):
        why = "after replying (brief not verbatim)"
    else:
        why = "(no reply)"
    return f"- {name}: judge {v['judge']} attempt {v['agent_id']} voided {why}"


def _round_lines(n: RoundNotes) -> list[str]:
    out = []
    if n.suspect_jumps:
        out.append(
            f"- {n.name}: paired jump of {SUSPECT_JUMP} or more on "
            f"{', '.join(n.suspect_jumps)}; verify by hand before believing it"
        )
    for role, found in sorted(n.injection.items()):
        out.append(f"- {n.name}: the {role} item addresses a judge {found}")
    for rj in n.rejected:
        out.append(f"- {n.name}: judge {rj['judge']} rejected: {'; '.join(rj['errors'][:3])}")
    out += [_void_line(n.name, v) for v in n.voided]
    for j, chars in sorted(n.prompt_extras.items()):
        out.append(
            f"- {n.name}: judge {j}'s prompt carried {chars} characters beyond its brief; "
            "read what was added"
        )
    for oq in n.one_quote:
        out.append(
            f"- {n.name}: judge {oq} quoted the same words for every dimension of the "
            "candidate; its scores may not rest on distinct evidence"
        )
    return out + _source_lines(n)


def other_runs_note(names: list[str]) -> str:
    """The note on the runs beside this one: each is a fresh panel, so each needs a reason.

    Args:
        names: The other runs' names.
    """
    return (
        f"{len(names)} other run(s) beside this one ({', '.join(names)}): "
        "a new run is a fresh panel; say why each was started"
    )


def _notes(run: Path, ledger: LedgerView, d: Decision, view: ResultFile | None) -> list[str]:
    out = _open_findings(view) + _repeat_test(ledger, d)
    others = view["flags"]["other_runs"] if view is not None else other_runs(run)
    if others:
        out.append("- " + other_runs_note(others))
    for r in ledger["rounds"]:
        notes = _from_view(view, r["round"]) if view and r.get("sealed") else _from_round(r)
        out += _round_lines(notes)
    return out + _panel_env_note(view)


def _repeat_test(ledger: LedgerView, d: Decision) -> list[str]:
    """The same text judged by a loop round and the sealed panel: measured repeat-test noise."""
    if not sealed_done(d):
        return []
    f = d["final"]
    same = [
        r
        for r in ledger["rounds"]
        if not r.get("sealed") and r.get("candidate_hash") == f.get("candidate_hash")
    ]
    if not same:
        return []
    prev = same[-1]
    moves = [
        f"{did} {prev['personas'][pid]['dimensions'][did]['median']:g} -> {e['median']:g}"
        for pid, p in f["personas"].items()
        for did, e in p["dimensions"].items()
    ]
    return [
        f"- repeat-test on the same text (round {prev['round']} to sealed): " + ", ".join(moves)
    ]


def _panel_env_note(view: ResultFile | None) -> list[str]:
    changed = view["flags"]["panel_env_changed"] if view is not None else []
    if not changed:
        return []
    return [
        f"- sealed panel: the judge command ran under different values of {', '.join(changed)} "
        "than in the loop rounds (another model or key): the loop's scores and the sealed grade "
        "come from different judges"
    ]


def _source_lines(n: RoundNotes) -> list[str]:
    """What the report says about where a round's replies came from."""
    out = []
    if n.pasted:
        out.append(f"- {n.name}: some replies were pasted, not read from a transcript")
    if n.runner:
        out.append(
            f"- {n.name}: some replies were read by sealed-panel from the judge command "
            "(judge.command in the settings above)"
        )
    if n.env_changed:
        out.append(
            f"- {n.name}: the judge command ran under different values of "
            f"{', '.join(n.env_changed)} across attempts (another model or key between judges): "
            "the declared judge model does not cover every attempt; put the model in "
            "judge.command to freeze it"
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
    view = build(run, spec, cfg, ledger)  # the result file's content, the same function
    decision = view["decision"] if view is not None else d["decision"]
    n_ctx = len(used_agent_ids(run))
    changed = view["settings"] if view is not None else overrides(cfg, spec)
    ref: Mapping[str, object] = spec.get("template") or {}
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
    if decision == BLOCKED:
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
        f"decision **{decision}**, {n_ctx} judge contexts, {template}{settings}.{ack}",
        "",
    ]
    if view is not None:
        out += _sealed_headline(view, model_line(view) + acknowledgement_line(view))
    out += _round_table(spec, ledger)
    out += _notes(run, ledger, d, view)
    return "\n".join(out) + "\n"


def grade_line(result: GradeResult) -> str:
    """What `grade` prints for its outcome: the sealed grade and its verdict, or why there is
    none that stands.

    Args:
        result: What grade() returned.
    """
    if not result.stands:
        return f"{result.decision}: {result.why}"
    cells = "; ".join(f"{did} {_cell(e)}" for p in result.grade.values() for did, e in p.items())
    return f"{result.decision}: {cells}. Verdict: {result.verdict}."


def outcome_line(r: Attempt) -> str:
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
