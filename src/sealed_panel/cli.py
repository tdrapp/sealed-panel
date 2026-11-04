"""Command line: `sealed-panel <command> RUN ...`. Every refusal exits with status 2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .aggregate import aggregate_round
from .brief import write_briefs
from .config import load_config
from .errors import EvalError, MalformedReply
from .record import last_reply, record_measurement, record_reply, transcript_agent_id
from .report import feedback_path, render_report, status
from .spec import init_run, run_config

DESCRIPTION = "Grade a text with blind panels of LLM judges and a sealed final panel."
ROUND_HELP = "round number; optional with --sealed, where it defaults to 1"


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
    return ap


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
        print(line + "; run `status`")
    elif a.cmd == "status":
        print(json.dumps(status(a.run), ensure_ascii=False, indent=2))
    elif a.cmd == "report":
        print(render_report(a.run), end="")


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
