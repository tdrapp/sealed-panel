"""The MCP server: the sealed-panel commands as tools for an agent host.

`sealed-panel mcp` serves these tools over stdio and exits when stdin closes. It needs the
`mcp` extra. Every tool calls the same functions as the command line, so every refusal is the
same. A refusal or a file the caller can fix comes back as a tool error with its reason; any
other error is masked.

The caller is the author of the text, so it is trusted with nothing that decides a grade. The
configuration is read once at startup from a file outside the root, and every tool that changes
a run refuses one frozen with other settings (the read-only tools still read it). Paths come
from the tool parameters and stay under the root (`--root`, `[mcp] root`, or the current
directory), never from the host. A transcript is proof only under a `--transcript-root`; one
that overlaps the root is turned off, and `status` says why: a file the caller could write is
no proof. Learning (rubric candidates, the user's own read) is not a tool: it stays on the
command line, where a person decides."""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

import anyio
from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from . import __version__
from . import library as rubrics_lib
from .aggregate import aggregate_round
from .brief import write_briefs
from .config import Config, config_differences, library_dir, load_config
from .errors import ConfigError, EvalError, SeatTaken, user_message
from .record import record_from, record_measurement
from .report import feedback_path, render_report
from .report import status as run_status
from .runner import judge_seat, open_seats
from .spec import TODO_MARKER, run_config
from .spec import init_run as init_any_run
from .store import contained, locked, outside_runs, resolve_round

# The longest a `judge` call may run. A judge process already started when it expires still
# finishes and is recorded; `status` shows the round.
JUDGE_TOOL_TIMEOUT_S = 3600.0

INSTRUCTIONS = """\
sealed-panel grades a text with blind panels of fresh judges and a sealed final panel. You
orchestrate; the tools refuse every step that would let a grade be re-rolled. Never grade the
text yourself and never report a number the sealed panel did not give.

A run: init (freeze the spec), brief (one blind brief per judge), then either judge (the
configured judge command answers each brief) or launch one fresh judge per brief yourself,
with the brief file's text verbatim as its whole prompt and no tools, and record each reply
(a transcript is better proof than a pasted reply). measure the instruments, aggregate,
status. Revise from the findings file only while status says CONTINUE; then brief the sealed
panel (sealed=true), judge or record it, aggregate it, and give the user the report.

To start from a rubric template: rubrics_suggest for the kind of text, then rubrics_use to
write a spec skeleton; fill every TODO marker before init."""

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
ADDS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
RUNS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)

T = TypeVar("T")

RunPath = Annotated[str, Field(description="the run directory")]
RoundNo = Annotated[
    int | None, Field(description="round number; optional for the sealed panel (1)")
]
Sealed = Annotated[bool, Field(description="the sealed panel instead of a loop round")]
LibraryPath = Annotated[
    str | None,
    Field(description="user rubric library; default: $SEALED_PANEL_RUBRICS, else the user's"),
]


class InitResult(BaseModel):
    """A frozen run."""

    run: str
    rubric_sha256: str


class BriefResult(BaseModel):
    """The briefs of one round, one per judge."""

    run: str
    round: int
    sealed: bool
    briefs: list[str] = Field(description="one brief file per judge, in judge order")


class RecordResult(BaseModel):
    """One recorded attempt: a valid reply, a rejected one, or a void."""

    judge: int
    agent_id: str
    status: Literal["VALID", "REJECTED", "VOID"]
    errors: list[str]
    source: str


class SeatResult(RecordResult):
    """One judge process started by the runner, and what was recorded."""

    duration_s: float
    exit_code: int | None
    stderr_log: str


class JudgeResult(BaseModel):
    """The open seats of a round, each answered by one fresh judge process."""

    run: str
    round: int
    sealed: bool
    seats: list[SeatResult]
    skipped: list[int] = Field(
        default_factory=list, description="seats another call took meanwhile (judged or filled)"
    )


class MeasureResult(BaseModel):
    """One instrument reading."""

    id: str
    value: str
    passed: bool


class AggregateResult(BaseModel):
    """An aggregated round. The author reads the findings file, not the numbers."""

    round: int
    sealed: bool
    rejected_or_void: int
    feedback: str | None = Field(None, description="the author's findings file (loop round)")


class StatusResult(BaseModel):
    """The stop rule's decision, read only."""

    decision: str
    overrides: list[str] = Field(description="every setting that differs from the defaults")
    served: bool = Field(
        description="whether this server's configuration is the run's: only then do the tools "
        "that change a run accept it"
    )
    transcripts: str = Field(
        description="where `record` reads transcripts from, or why it reads none"
    )
    details: dict[str, Any]


class ReportResult(BaseModel):
    """The run's report, in markdown."""

    markdown: str


class Suggestion(BaseModel):
    """A template for the kind of text: when to use it, its dimensions."""

    id: str
    version: int
    source: str
    when_to_use: str
    dimensions: list[tuple[str, str]]


class SuggestResult(BaseModel):
    """The templates for a kind of text, best first; empty when none matches."""

    kind: str
    suggestions: list[Suggestion]
    all_templates: dict[str, str] = Field(
        default_factory=dict, description="every template's when_to_use, when none matched"
    )


class UseResult(BaseModel):
    """A spec skeleton written from a template. `init` refuses it until every marker is
    filled."""

    path: str
    template: str
    todo_marker: str = Field(description="the marker left in every field a person fills")


def _calling(fn: Callable[[], T]) -> T:
    """Run one core call; an error the caller can fix becomes a tool error with its reason."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:
        message = user_message(exc)
        if message is None:
            raise
        raise ToolError(message) from exc


def _seat(
    run: Path, rnd: int, seat: int, sealed: bool, expected: dict[str, Any]
) -> dict[str, Any] | None:
    """One seat judged by the runner, or None when another call took it meanwhile. A judge
    started before a cancelled call ends still finishes and is recorded, in this thread."""
    try:
        return judge_seat(run, rnd, seat, sealed, expected)
    except SeatTaken:
        return None


def _record_result(r: dict[str, Any]) -> RecordResult:
    state: Literal["VALID", "REJECTED", "VOID"] = (
        "VOID" if r.get("void") else "VALID" if r["ok"] else "REJECTED"
    )
    return RecordResult(
        judge=r["judge"],
        agent_id=r["agent_id"],
        status=state,
        errors=list(r["errors"]),
        source=str(r.get("source")),
    )


def init_run(
    run: Path,
    spec_path: Path,
    cfg: Config | None = None,
    library: Path | None = None,
    facts_root: Path | None = None,
) -> str:
    """`init` for the MCP caller, who is the author: a spec that acknowledges injection flags
    is refused, since acknowledging one is a person's decision (see spec.init_run).

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file.
        cfg: The server's configuration.
        library: The user rubric library; the default one when None.
        facts_root: The server's root: a `facts_file` must resolve under it.
    """
    return init_any_run(run, spec_path, cfg, library, facts_root, acknowledge=False)


# The lock the init tool holds, at the root: the one file the server owns there.
INIT_LOCK = ".sealed-panel-init.lock"


def _reserve(base: Path) -> Path:
    """Create the root's init lock at startup, before any tool runs, so no caller can put
    anything else at its name; refuse to start when something other than a file holds it."""
    lock = base / INIT_LOCK
    if lock.is_symlink() or (lock.exists() and not lock.is_file()):
        raise ConfigError(
            f"{lock} is not a regular file: it is the server's own lock file; remove what is "
            "there, then start the server again"
        )
    try:
        os.close(os.open(lock, os.O_RDWR | os.O_CREAT, 0o600))
    except OSError as exc:
        raise ConfigError(f"cannot create the server's lock file {lock}: {exc.strerror}") from exc
    return lock


def names_lock(where: Path, base: Path, lock: Path) -> bool:
    """Whether a resolved path under the root is the root's lock file or under it, compared by
    inode on the entry right under the root: a name that differs only in case, on a
    case-insensitive filesystem, or a hard link, is the lock too.

    Args:
        where: The resolved path, under `base`.
        base: The root.
        lock: The lock file, directly under the root.
    """
    if where == base:
        return False
    top = base / where.relative_to(base).parts[0]
    try:
        return top.samefile(lock)
    except OSError:
        return False  # nothing there yet: not the lock, which exists from startup


def build_server(
    root: Path | None = None,
    config: Path | None = None,
    transcript_roots: list[Path] | None = None,
) -> FastMCP:
    """The sealed-panel MCP server. Every path a tool takes resolves under the root: `root`,
    else `[mcp] root` in `config`, else the current directory. `config` is the user's
    configuration: `init` freezes it into every run, and `judge` refuses a run whose judge
    settings differ from it."""
    config_file = config.expanduser().resolve() if config else None
    server = load_config(config_file)  # read once: the caller may write under the root
    configured = str(server.section("mcp")["root"]).strip()
    base = (root or (Path(configured) if configured else Path.cwd())).expanduser().resolve()
    if config_file is not None and config_file.is_relative_to(base):
        raise ConfigError(
            f"the server's configuration {config_file} is under the root {base}: the caller "
            "could rewrite it. Keep it outside the root."
        )
    allowed: list[Path] = []
    turned_off: list[str] = []  # why a transcript root given is not used
    for r in transcript_roots or []:
        real = r.expanduser().resolve()
        if real.is_relative_to(base) or base.is_relative_to(real):
            turned_off.append(
                f"the transcript root {real} overlaps the root {base}, so the caller could write "
                "a transcript there and it would be no proof; start the host from a project "
                "directory, not from a folder that holds the transcripts"
            )
            continue
        allowed.append(r.expanduser())
    for reason in turned_off:
        logging.getLogger("sealed_panel").warning("transcripts off: %s", reason)

    # Held by the init tool: a dot-file, so no run is named after it.
    init_lock = _reserve(base)

    def path(p: str) -> Path:
        """A path a caller gave, resolved (links followed) and confined to the root."""
        if p.startswith("~"):
            raise ToolError(f"{p!r}: paths are relative to the root {base}, never under ~")
        given = Path(p)
        try:
            where = contained(given if given.is_absolute() else base / given, base, repr(p))
        except EvalError as exc:
            raise ToolError(str(exc)) from exc
        if names_lock(where, base, init_lock):
            raise ToolError(f"{p!r} is the server's own lock file: name another path")
        return where

    def opt(p: str | None) -> Path | None:
        return path(p) if p else None

    def served(p: str) -> Path:
        """A run this server serves: frozen with the settings it was started with."""
        where = path(p)
        differ = config_differences(run_config(where), server)
        if differ:
            raise EvalError(
                f"this run's settings differ from the configuration the server was started with "
                f"({', '.join(differ)}): start a server with that configuration"
            )
        return where

    def readable(p: str) -> Path:
        """A run a read-only tool may read, whatever its settings (its lock and shape are
        checked by the read itself): a read changes nothing, and the report lists the run's own
        settings next to the grade."""
        return path(p)

    def transcripts_note() -> str:
        if allowed:
            return "read only under " + ", ".join(str(r.resolve()) for r in allowed)
        return "off: " + ("; ".join(turned_off) if turned_off else "no --transcript-root given")

    def transcript_path(p: str) -> Path:
        """A judge's transcript, read only: under a transcript root, without any link."""
        if not allowed:
            raise ToolError(
                "this server takes no transcript: "
                + (
                    "; ".join(turned_off)
                    if turned_off
                    else "start it with --transcript-root DIR, "
                    "the folder where your host writes them"
                )
            )
        for r in allowed:
            with contextlib.suppress(EvalError):
                return contained(Path(p), r)
        raise ToolError(f"{p!r} is outside every transcript root: a transcript is proof only there")

    mcp = FastMCP(
        "sealed-panel",
        instructions=INSTRUCTIONS,
        version=__version__,
        mask_error_details=True,
        strict_input_validation=True,
    )

    @mcp.tool(annotations=ADDS)
    def init(
        run: RunPath,
        spec: Annotated[str, Field(description="the spec JSON file")],
        library: LibraryPath = None,
    ) -> InitResult:
        """Validate a spec and freeze it into a new run directory, with the configuration the
        server was started with (its judge command included): a caller never picks its judge.
        A spec carrying injection_acknowledged is refused: a person acknowledges a flag, at the
        command line."""

        def call() -> InitResult:
            where = path(run)
            # One init at a time under this root: a run's checks (no run among its ancestors,
            # an empty or new directory) then hold against a concurrent init.
            with locked(init_lock, "another init is running", wait=True):
                digest = init_run(where, path(spec), server, opt(library), base)
            return InitResult(run=str(where), rubric_sha256=digest)

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def brief(
        run: RunPath,
        artefact: Annotated[str, Field(description="the candidate text file")],
        control: Annotated[str, Field(description="the deliberately bad version")],
        round: RoundNo = None,
        sealed: Annotated[
            bool,
            Field(
                description="brief the sealed panel: after CONVERGED, PLATEAU or CAP, or as a "
                "one-shot grade; the control is the loop's"
            ),
        ] = False,
        good: Annotated[str | None, Field(description="a known-good the user validated")] = None,
    ) -> BriefResult:
        """Write one blind brief per judge: for a loop round, or for the sealed panel."""

        def call() -> BriefResult:
            rnd = resolve_round(round, sealed)
            where = served(run)
            paths = write_briefs(where, rnd, path(artefact), path(control), opt(good), sealed)
            return BriefResult(
                run=str(where), round=rnd, sealed=sealed, briefs=list(map(str, paths))
            )

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def record(
        run: RunPath,
        judge: Annotated[int, Field(description="the judge slot, as in brief_j<J>.md")],
        agent_id: Annotated[str, Field(description="the judge's context id, new in the run")],
        round: RoundNo = None,
        sealed: Sealed = False,
        transcript: Annotated[
            str | None, Field(description="the judge's JSONL transcript (best proof)")
        ] = None,
        reply_file: Annotated[str | None, Field(description="the reply saved to a file")] = None,
        reply: Annotated[str | None, Field(description="the reply text, unchanged")] = None,
        void: Annotated[
            bool,
            Field(
                description="the agent received the brief and never replied (needs its "
                "transcript, holding the brief id); a void never fills a seat"
            ),
        ] = False,
    ) -> RecordResult:
        """Validate and store one judge's reply: from its transcript, or pasted (flagged). With
        void, record an agent that received a brief and never replied."""

        def call() -> RecordResult:
            given = [x for x in (transcript, reply_file, reply) if x is not None]
            if len(given) != 1:
                raise ToolError("give exactly one of transcript, reply_file or reply")
            text = reply if reply is not None else None
            if reply_file is not None:
                text = path(reply_file).read_text("utf-8")
            rnd = resolve_round(round, sealed)
            where = served(run)
            proof = transcript_path(transcript) if transcript is not None else None
            r = record_from(where, rnd, judge, agent_id, sealed, proof, text, void)
            return _record_result(r)

        return _calling(call)

    @mcp.tool(annotations=RUNS, timeout=JUDGE_TOOL_TIMEOUT_S)
    async def judge(
        ctx: Context,
        run: RunPath,
        round: RoundNo = None,
        sealed: Sealed = False,
        judges: Annotated[
            list[int] | None, Field(description="only these judge slots (default: every open one)")
        ] = None,
    ) -> JudgeResult:
        """Run the run's judge command once per open seat: one fresh process per brief, the brief
        on stdin, the reply on stdout. Split a large panel across calls with `judges`."""
        rnd = _calling(lambda: resolve_round(round, sealed))
        where = _calling(lambda: served(run))
        expected = server.judge
        seats = _calling(lambda: open_seats(where, rnd, sealed, judges, expected))
        done: list[SeatResult] = []
        skipped: list[int] = []
        for i, seat in enumerate(seats, start=1):
            call = functools.partial(_seat, where, rnd, seat, sealed, expected)
            r = await anyio.to_thread.run_sync(_calling, call, abandon_on_cancel=True)
            if r is None:
                skipped.append(seat)
                await ctx.report_progress(i, len(seats), f"judge {seat}: taken by another call")
                continue
            proof = r["runner"]
            row = SeatResult(
                **_record_result(r).model_dump(),
                duration_s=proof["duration_s"],
                exit_code=proof["exit_code"],
                stderr_log=proof["stderr_log"],
            )
            done.append(row)
            await ctx.report_progress(i, len(seats), f"judge {seat}: {row.status}")
        return JudgeResult(run=str(where), round=rnd, sealed=sealed, seats=done, skipped=skipped)

    @mcp.tool(annotations=ADDS)
    def measure(
        run: RunPath,
        id: Annotated[str, Field(description="a measured criterion of the spec")],
        value: Annotated[str, Field(description="the reading")],
        passed: Annotated[
            bool | None, Field(description="only for a criterion without a numeric threshold")
        ] = None,
        round: RoundNo = None,
        sealed: Sealed = False,
    ) -> MeasureResult:
        """Record one instrument reading, once per criterion and round, before aggregating."""

        def call() -> MeasureResult:
            rnd = resolve_round(round, sealed)
            ok = record_measurement(served(run), rnd, id, value, passed, sealed)
            return MeasureResult(id=id, value=value, passed=ok)

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def aggregate(run: RunPath, round: RoundNo = None, sealed: Sealed = False) -> AggregateResult:
        """Aggregate a round once. A loop round writes the author's findings file."""

        def call() -> AggregateResult:
            rnd = resolve_round(round, sealed)
            e = aggregate_round(served(run), rnd, sealed)
            return AggregateResult(
                round=e["round"],
                sealed=e["sealed"],
                rejected_or_void=len(e["rejected_judges"]) + len(e["voided"]),
                feedback=None if sealed else str(feedback_path(path(run), e["round"])),
            )

        return _calling(call)

    @mcp.tool(annotations=READ)
    def status(run: RunPath) -> StatusResult:
        """The stop rule's decision: CONTINUE, CONVERGED, PLATEAU, CAP, SEALED, or BLOCKED BY
        FLAG when the candidate addresses a judge and the spec does not acknowledge it."""
        where = _calling(lambda: readable(run))
        d = _calling(lambda: run_status(where))
        rest = {k: v for k, v in d.items() if k not in ("decision", "overrides")}
        differ = _calling(lambda: config_differences(run_config(where), server))
        return StatusResult(
            decision=d["decision"],
            overrides=d["overrides"],
            served=not differ,
            transcripts=transcripts_note(),
            details=rest,
        )

    @mcp.tool(annotations=READ)
    def report(run: RunPath) -> ReportResult:
        """The run's report: the sealed grade, the round table and every note."""
        return ReportResult(markdown=_calling(lambda: render_report(readable(run))))

    @mcp.tool(annotations=READ)
    def rubrics_suggest(
        kind: Annotated[str, Field(description="the kind of text, such as email or cv")],
        text: Annotated[
            str | None, Field(description="the text file; its keywords only break ties")
        ] = None,
        top: Annotated[int, Field(ge=1, description="how many templates")] = 3,
        library: LibraryPath = None,
    ) -> SuggestResult:
        """The rubric templates for a kind of text, with when to use each and its dimensions."""

        def call() -> SuggestResult:
            lib = library_dir(opt(library))
            body = path(text).read_text("utf-8") if text else ""
            rows = rubrics_lib.suggest(lib, kind, body, top)
            every = (
                {}
                if rows
                else {
                    tid: str(s["template"]["when_to_use"])
                    for tid, s in rubrics_lib.all_templates(lib)[0].items()
                }
            )
            return SuggestResult(
                kind=kind,
                suggestions=[
                    Suggestion(
                        id=r["id"],
                        version=int(r["version"]),
                        source=str(r["source"]),
                        when_to_use=str(r["when_to_use"]),
                        dimensions=[(str(d), str(q)) for d, q in r["dimensions"]],
                    )
                    for r in rows
                ],
                all_templates=every,
            )

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def rubrics_use(
        id: Annotated[str, Field(description="the template id, from rubrics_suggest")],
        out: Annotated[str, Field(description="where to write the spec; refused if it exists")],
        persona_file: Annotated[
            str | None, Field(description="a JSON list of personas (id, description)")
        ] = None,
        library: LibraryPath = None,
    ) -> UseResult:
        """Write a spec skeleton from a template, under the root. A person fills every TODO
        marker it leaves (the facts, the personas, the choices), then init freezes it."""

        def call() -> UseResult:
            lib = library_dir(opt(library))
            target = outside_runs(path(out), "the spec")
            written = rubrics_lib.write_skeleton(lib, id, target, opt(persona_file))
            return UseResult(path=str(written), template=id, todo_marker=TODO_MARKER)

        return _calling(call)

    return mcp


def usable_roots(values: list[str], home: str | None = None) -> list[Path]:
    """The transcript roots given. A host expands an unset value to nothing, or leaves its
    placeholder when it does not know it: neither names a folder. A leading `~`, `$HOME` or
    `${HOME}` is the user's home, since a host may pass any of them unexpanded."""
    home = home or str(Path.home())
    roots = []
    for value in values:
        v = value.strip()
        for prefix in ("${HOME}", "$HOME"):
            if v == prefix or v.startswith(prefix + "/"):
                v = home + v[len(prefix) :]
        if v == "~" or v.startswith("~/"):
            v = home + v[1:]
        if v and not v.startswith("$"):
            roots.append(Path(v))
    return roots


def serve(
    root: Path | None = None, config: Path | None = None, transcript_roots: list[str] | None = None
) -> None:
    """Serve over stdio until stdin closes. Logs go to stderr; stdout carries the protocol."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    server = build_server(root, config, usable_roots(transcript_roots or []))
    server.run(transport="stdio", show_banner=False)
