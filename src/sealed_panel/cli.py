"""Command line: `sealed-panel <command> RUN ...`. Every refusal exits with status 2."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, library
from .aggregate import aggregate_round
from .brief import write_briefs
from .config import LIBRARY_ENV, library_dir, load_config
from .decide import sealed_done
from .errors import EvalError, SeatTaken, user_message
from .learn import learn_after_seal, user_disagreement
from .record import outcome_line, record_from, record_measurement
from .report import feedback_path, render_report, status
from .runner import judge_seat, open_seats
from .spec import TODO_MARKER, init_run
from .store import load_json, resolve_round

# The usual exit status of a command stopped by Ctrl-C.
INTERRUPTED = 130
DESCRIPTION = "Grade a text with blind panels of LLM judges and a sealed final panel."
ROUND_HELP = "round number; optional with --sealed, where it defaults to 1"
LIBRARY_HELP = f"user library directory (default: ${LIBRARY_ENV}, else the user-level default)"


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
    p.add_argument("--library", type=Path, help=LIBRARY_HELP)

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

    p = sub.add_parser(
        "judge", help="run the configured judge command once for each open seat of a round"
    )
    _round_args(p)
    p.add_argument(
        "--judge", type=int, action="append", help="only this judge slot (repeat for several)"
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

    p = sub.add_parser("learning", help="learning from sealed runs (a person decides)")
    lsub = p.add_subparsers(dest="lcmd", required=True)
    r = lsub.add_parser("learn", help="write a sealed run's rubric candidates to the inbox")
    r.add_argument("run", type=Path)
    r.add_argument("--library", type=Path, help=LIBRARY_HELP)

    p = sub.add_parser("mcp", help="serve the commands as MCP tools over stdio (the mcp extra)")
    p.add_argument(
        "--root",
        type=Path,
        help="every tool path must resolve under it (default: [mcp] root, else here)",
    )
    p.add_argument(
        "--config",
        type=Path,
        help="the configuration of every run this server serves, its judge command included; "
        "read once, and it must live outside the root",
    )
    p.add_argument(
        "--transcript-root",
        action="append",
        default=[],
        nargs="?",
        const="",
        metavar="DIR",
        help="a folder where the host writes judge transcripts, read only, outside the root "
        "(repeat for several); a transcript elsewhere is refused",
    )

    p = sub.add_parser("rubrics", help="the rubric library: templates and learned candidates")
    rsub = p.add_subparsers(dest="rcmd", required=True)

    def lib_args(r: argparse.ArgumentParser) -> None:
        r.add_argument("--library", type=Path, help=LIBRARY_HELP)

    r = rsub.add_parser("list", help="templates (built-in and user) and open candidates")
    lib_args(r)
    r = rsub.add_parser("suggest", help="the templates for a kind of text")
    r.add_argument("--kind", required=True)
    r.add_argument("--text", type=Path, help="the text; its keywords only break ties")
    r.add_argument("--top", type=int, default=3)
    lib_args(r)
    r = rsub.add_parser("show", help="print a template")
    r.add_argument("id")
    lib_args(r)
    r = rsub.add_parser("use", help="write a spec skeleton from a template")
    r.add_argument("id")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--persona-file", type=Path, help="JSON list of personas (id, description)")
    lib_args(r)
    r = rsub.add_parser("promote", help="apply a candidate to a user template (a person decides)")
    r.add_argument("candidate")
    r.add_argument("--into", help="template id; takes precedence over the candidate's template")
    r.add_argument("--yes", action="store_true", help="confirm without a prompt (no terminal)")
    lib_args(r)
    r = rsub.add_parser("reject", help="close a candidate with a reason")
    r.add_argument("candidate")
    r.add_argument("--reason", required=True)
    lib_args(r)
    return ap


def _warn(warnings: list[str]) -> None:
    for w in warnings:
        print(f"sealed-panel: warning: {w}", file=sys.stderr)


def _confirm(a: argparse.Namespace, plan: dict[str, object]) -> None:
    for line in library.side_by_side(plan["current"], plan["updated"]):  # type: ignore[arg-type]
        print(line)
    print(f"writes {plan['target']}")
    if a.yes:
        return
    if not sys.stdin.isatty():
        raise EvalError("promote needs a person: run it in a terminal, or pass --yes")
    try:
        answer = input(f"type the candidate id ({a.candidate}) to promote it: ").strip()
    except EOFError:
        answer = ""
    if answer != a.candidate:
        raise EvalError("not confirmed: nothing was written")


def _rubrics(a: argparse.Namespace) -> None:
    lib = library_dir(a.library)
    if a.rcmd == "list":
        print(f"library: {lib}")
        served, warnings = library.all_templates(lib)
        for tid, srv in served.items():
            t = srv["template"]
            print(f"{tid:18} v{t['version']:<3} {srv['source']:30} {t['title']}")
        cands, more = library.open_candidates(lib)
        _warn(warnings + more)
        print(f"inbox: {len(cands)} open candidate(s)")
        for c in cands:
            tpl = c.get("template") or {}
            name = f"{tpl.get('id')} v{tpl.get('version')}" if tpl else "-"
            print(
                f"  {c['id']}  {c['kind']:19} template {name:18} {c['dimension']} (x{c['count']})"
            )
            for before in c.get("rejected_before") or []:
                version = (before.get("template") or {}).get("version")
                print(f"      rejected before (v{version}): {before.get('reason')}")
    elif a.rcmd == "suggest":
        text = a.text.read_text(encoding="utf-8") if a.text else ""
        rows = library.suggest(lib, a.kind, text, a.top)
        if not rows:
            print(f"no template for kind {a.kind!r}. The templates and when to use each:")
            for tid, srv in library.all_templates(lib)[0].items():
                print(f"  {tid}: {srv['template']['when_to_use']}")
        for row in rows:
            print(f"{row['id']} v{row['version']} ({row['source']}): {row['when_to_use']}")
            for did, question in row["dimensions"]:
                print(f"  {did:14} {question}")
    elif a.rcmd == "show":
        srv = library.get_template(lib, a.id)
        t = srv["template"]
        print(f"# {a.id} v{t['version']}, {srv['source']}")
        print(json.dumps(t, ensure_ascii=False, indent=2))
    elif a.rcmd == "use":
        out = library.write_skeleton(lib, a.id, a.out, a.persona_file)
        print(f"wrote {out}: fill every {TODO_MARKER} marker, then run init")
    elif a.rcmd == "promote":
        plan = library.plan_promotion(lib, a.candidate, a.into)
        _confirm(a, plan)
        path = library.apply_promotion(lib, plan)
        print(f"promoted {a.candidate} into {path} (v{load_json(path)['version']})")
    elif a.rcmd == "reject":
        path = library.reject(lib, a.candidate, a.reason)
        print(f"rejected {a.candidate}: moved to {path}")


def _mcp(a: argparse.Namespace) -> None:
    try:
        from .mcp_server import serve
    except ImportError as exc:
        raise EvalError(
            f"the MCP server needs the mcp extra ({exc}): pip install "
            f"'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v{__version__}'"
        ) from exc
    serve(a.root, a.config, a.transcript_root)


def _round(a: argparse.Namespace) -> int:
    return resolve_round(a.round, a.sealed)


def _record(a: argparse.Namespace) -> str:
    rnd = _round(a)
    output = None
    if a.output is not None:
        output = sys.stdin.read() if a.output == "-" else Path(a.output).read_text("utf-8")
    r = record_from(a.run, rnd, a.judge, a.agent_id, a.sealed, a.transcript, output, a.void)
    return outcome_line(r)


def _run(a: argparse.Namespace) -> None:
    if a.cmd == "init":
        cfg = load_config(a.config) if a.config else None
        print(f"frozen: rubric sha256 {init_run(a.run, a.spec, cfg, a.library)}")
    elif a.cmd == "brief":
        for path in write_briefs(a.run, _round(a), a.artefact, a.control, a.good, a.sealed):
            print(path)
    elif a.cmd == "record":
        print(_record(a))
    elif a.cmd == "judge":
        rnd = _round(a)
        for seat in open_seats(a.run, rnd, a.sealed, a.judge):
            try:
                r = judge_seat(a.run, rnd, seat, a.sealed)
            except SeatTaken as exc:
                print(f"SKIPPED judge {seat}: {exc}")
                continue
            proof = r["runner"]
            print(f"{outcome_line(r)} ({proof['duration_s']:g} s, stderr in {proof['stderr_log']})")
    elif a.cmd == "measure":
        ok = record_measurement(a.run, _round(a), a.id, a.value, a.passed, a.sealed)
        print(f"measured {a.id} = {a.value} ({'pass' if ok else 'fail'})")
    elif a.cmd == "aggregate":
        e = aggregate_round(a.run, _round(a), a.sealed)
        line = (
            f"aggregated round {e['round']}{' (sealed)' if e['sealed'] else ''}: "
            f"{len(e['rejected_judges']) + len(e['voided'])} rejected or void attempt(s)"
        )
        if not e["sealed"]:
            line += f"; findings in {feedback_path(a.run, e['round'])}"
        print(line + "; run `status`")
    elif a.cmd == "status":
        print(json.dumps(status(a.run), ensure_ascii=False, indent=2))
    elif a.cmd == "report":
        print(render_report(a.run), end="")
    elif a.cmd == "feedback":
        lib = library_dir(a.library) if a.library else None
        path = user_disagreement(a.run, a.note, a.dimension, lib)
        print(f"recorded your read as candidate {path.stem} in {path.parent}")
    elif a.cmd == "learning":
        if not sealed_done(status(a.run)):
            raise EvalError("learning reads a sealed panel: seal the run first")
        lib = library_dir(a.library)
        paths = learn_after_seal(a.run, lib)
        print(f"{len(paths)} rubric candidate(s) in {lib / 'inbox'}")
    elif a.cmd == "rubrics":
        _rubrics(a)
    elif a.cmd == "mcp":
        _mcp(a)


def main(argv: list[str] | None = None) -> int:
    """Run the `sealed-panel` command and return its exit status.

    Args:
        argv: The arguments; the process's own when None.
    """
    a = _parser().parse_args(argv)
    try:
        _run(a)
    except KeyboardInterrupt:
        print("sealed-panel: interrupted; a judge that was running is recorded as void")
        return INTERRUPTED
    except Exception as exc:
        message = user_message(exc)
        if message is None:
            raise
        print(f"sealed-panel: {message}", file=sys.stderr)
        return 2
    return 0
