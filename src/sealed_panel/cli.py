"""Command line: `sealed-panel <command> RUN ...`. Every refusal exits with status 2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, library
from .aggregate import aggregate_round
from .brief import write_briefs
from .config import load_config
from .errors import EvalError, MalformedReply
from .learn import user_disagreement
from .record import last_reply, record_measurement, record_reply, transcript_agent_id
from .report import feedback_path, render_report, status
from .spec import TODO_MARKER, init_run, run_config
from .store import load_json

DESCRIPTION = "Grade a text with blind panels of LLM judges and a sealed final panel."
ROUND_HELP = "round number; optional with --sealed, where it defaults to 1"
LIBRARY_HELP = (
    f"user library directory (default: ${library.ENV_VAR}, else the library.path setting)"
)


def _round_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("run", type=Path)
    p.add_argument("--round", type=int, help=ROUND_HELP)
    p.add_argument("--sealed", action="store_true")


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="sealed-panel", description=DESCRIPTION)
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="validate and freeze a spec")
    p.add_argument("run", type=Path)
    p.add_argument("--spec", type=Path, required=True)
    p.add_argument(
        "--config", type=Path, help="TOML file overriding defaults.toml, frozen into the run"
    )

    p = sub.add_parser("brief", help="write one blind brief per judge")
    _round_args(p)
    p.add_argument("--artefact", type=Path, required=True)
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--good", type=Path)

    p = sub.add_parser("record", help="validate and store one judge's reply")
    _round_args(p)
    p.add_argument("--judge", type=int, required=True)
    p.add_argument("--agent-id", required=True)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--transcript",
        type=Path,
        help="the judge's JSONL transcript: its last assistant text is the reply",
    )
    src.add_argument("--output", help="the judge's reply as a file, or - for stdin")
    p.add_argument(
        "--void",
        action="store_true",
        help="the agent received the brief but never replied (needs --transcript)",
    )

    p = sub.add_parser("measure", help="record an instrument reading")
    _round_args(p)
    p.add_argument("--id", required=True)
    p.add_argument("--value", required=True)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--pass", dest="passed", action="store_const", const=True)
    g.add_argument("--fail", dest="passed", action="store_const", const=False)

    p = sub.add_parser("aggregate", help="aggregate a round once")
    _round_args(p)

    p = sub.add_parser("status", help="print the stop rule's decision")
    p.add_argument("run", type=Path)
    p = sub.add_parser("report", help="print the run's report")
    p.add_argument("run", type=Path)

    p = sub.add_parser("feedback", help="record your own read disagreeing with the sealed grade")
    p.add_argument("run", type=Path)
    p.add_argument("--note", required=True)
    p.add_argument("--dimension")
    p.add_argument("--library", type=Path, help=LIBRARY_HELP)

    p = sub.add_parser("rubrics", help="the rubric library: templates and learned candidates")
    rsub = p.add_subparsers(dest="rcmd", required=True)
    r = rsub.add_parser("list", help="templates (built-in and user) and open candidates")
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    r = rsub.add_parser("suggest", help="rank templates for a kind of text")
    r.add_argument("--kind", required=True)
    r.add_argument("--text", type=Path, help="the text, for keyword overlap")
    r.add_argument("--top", type=int, default=3)
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    r = rsub.add_parser("show", help="print a template")
    r.add_argument("id")
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    r = rsub.add_parser("use", help="write a spec skeleton from a template")
    r.add_argument("id")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--persona-file", type=Path, help="JSON list of personas (id, description)")
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    r = rsub.add_parser("promote", help="apply a candidate to a user template (a person decides)")
    r.add_argument("candidate")
    r.add_argument("--into", help="template id, when the candidate names none or another")
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    r = rsub.add_parser("reject", help="close a candidate with a reason")
    r.add_argument("candidate")
    r.add_argument("--reason", required=True)
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)
    return ap


def _rubrics(a: argparse.Namespace) -> None:
    lib = library.library_dir(a.library)
    if a.rcmd == "list":
        print(f"library: {lib}")
        for tid, (t, source) in library.all_templates(lib).items():
            print(f"{tid:18} v{t['version']:<3} {source:30} {t['title']}")
        cands = library.open_candidates(lib)
        print(f"inbox: {len(cands)} open candidate(s)")
        for c in cands:
            tpl = (c.get("template") or {}).get("id", "-")
            print(f"  {c['id']}  {c['kind']:19} template {tpl:16} {c['dimension']} (x{c['count']})")
    elif a.rcmd == "suggest":
        text = a.text.read_text(encoding="utf-8") if a.text else ""
        for row in library.suggest(lib, a.kind, text, a.top):
            print(
                f"{row['score']:4}  {row['id']} v{row['version']} ({row['source']}): "
                f"{row['reason']}"
            )
    elif a.rcmd == "show":
        t, source = library.get_template(lib, a.id)
        print(f"# {a.id} v{t['version']}, {source}")
        print(json.dumps(t, ensure_ascii=False, indent=2))
    elif a.rcmd == "use":
        out = library.write_skeleton(lib, a.id, a.out, a.persona_file)
        print(f"wrote {out}: fill every {TODO_MARKER} marker, then run init")
    elif a.rcmd == "promote":
        path = library.promote(lib, a.candidate, a.into)
        t = load_json(path)
        print(f"promoted {a.candidate} into {path} (v{t['version']})")
    elif a.rcmd == "reject":
        path = library.reject(lib, a.candidate, a.reason)
        print(f"rejected {a.candidate}: moved to {path}")


def _round(a: argparse.Namespace) -> int:
    if a.round is not None:
        return int(a.round)
    if a.sealed:
        return 1
    raise EvalError("--round is required for a loop round (it defaults to 1 only with --sealed)")


def _record(a: argparse.Namespace) -> str:
    rnd = _round(a)
    if a.void and not a.transcript:
        raise EvalError("--void needs --transcript")
    if a.transcript:
        frozen = run_config(a.run)
        found = transcript_agent_id(a.transcript, frozen)
        if found and found != a.agent_id:
            raise EvalError(
                f"--agent-id {a.agent_id} does not match the transcript's agent {found}"
            )
        raw, source, malformed = "", ("void" if a.void else "transcript"), ""
        if not a.void:
            try:
                raw = last_reply(a.transcript, frozen)
            except MalformedReply as exc:
                malformed = str(exc)
    else:
        raw = sys.stdin.read() if a.output == "-" else Path(a.output).read_text("utf-8")
        source, malformed = "output", ""
    r = record_reply(
        a.run, rnd, a.judge, a.agent_id, raw, a.sealed, source, a.transcript, a.void, malformed
    )
    if r.get("void"):
        return f"VOID judge {a.judge}: recorded as an attempt without a reply"
    return (
        ("VALID" if r["ok"] else "REJECTED")
        + f" judge {a.judge}"
        + ("" if r["ok"] else ": " + "; ".join(r["errors"][:3]))
    )


def _run(a: argparse.Namespace) -> None:
    if a.cmd == "init":
        cfg = load_config(a.config) if a.config else None
        print(f"frozen: rubric sha256 {init_run(a.run, a.spec, cfg)}")
    elif a.cmd == "brief":
        for path in write_briefs(a.run, _round(a), a.artefact, a.control, a.good, a.sealed):
            print(path)
    elif a.cmd == "record":
        print(_record(a))
    elif a.cmd == "measure":
        ok = record_measurement(a.run, _round(a), a.id, a.value, a.passed, a.sealed)
        print(f"measured {a.id} = {a.value} ({'pass' if ok else 'fail'})")
    elif a.cmd == "aggregate":
        e = aggregate_round(a.run, _round(a), a.sealed)
        line = (
            f"aggregated round {e['round']}{' (sealed)' if e['sealed'] else ''}: "
            f"{len(e['rejected_judges'])} rejected or void attempt(s)"
        )
        if not e["sealed"]:
            line += f"; findings in {feedback_path(a.run, e['round'])}"
        else:
            line += f"; {len(e['inbox'])} rubric candidate(s) in the library inbox"
        print(line + "; run `status`")
    elif a.cmd == "status":
        print(json.dumps(status(a.run), ensure_ascii=False, indent=2))
    elif a.cmd == "report":
        print(render_report(a.run), end="")
    elif a.cmd == "feedback":
        path = user_disagreement(a.run, a.note, a.dimension, a.library)
        print(f"recorded your read as candidate {path.stem} in {path.parent}")
    elif a.cmd == "rubrics":
        _rubrics(a)


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    try:
        _run(a)
    except (EvalError, OSError) as exc:
        print(f"sealed-panel: {exc}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError) as exc:
        print(
            f"sealed-panel: unusable input ({type(exc).__name__}: {exc}); "
            "check the files given to this command",
            file=sys.stderr,
        )
        return 2
    return 0
