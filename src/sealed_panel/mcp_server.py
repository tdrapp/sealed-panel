"""The MCP server: the sealed-panel commands as tools for an agent host.

`sealed-panel mcp` serves these tools over stdio and exits when stdin closes. It needs the
`mcp` extra (`pip install 'sealed-panel[mcp]'`). Every tool calls the same functions as the
command line, so every refusal is the same. A refusal or a file the caller can fix comes back
as a tool error with its reason; any other error is masked.

Paths come from the tool parameters, resolved against the server's root directory (the current
directory, or `--root`), never from the host. Promoting or rejecting a rubric candidate is not
a tool: a host can answer a confirmation on its own, so it proves no person decided. The tools
that touch candidates return the command a person runs instead.
"""

from __future__ import annotations

import functools
import logging
import shlex
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
from .config import Config, library_dir, load_config
from .errors import user_message
from .learn import user_disagreement
from .record import record_from, record_measurement
from .report import feedback_path, render_report
from .report import status as run_status
from .runner import SeatTaken, judge_seat, open_seats
from .spec import init_run, library_for
from .store import resolve_round

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
status. Revise from the findings file only while status says CONTINUE; then seal, judge or
record the sealed panel, aggregate it, and give the user the report.

Promoting or rejecting a rubric candidate is a person's decision: show the user the command
that rubrics_show or feedback returns, never run it."""

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
    Field(description="user rubric library; default: the run's, else the user library"),
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
    learning: str | None = Field(None, description="what learning wrote (sealed panel)")
    inbox: list[str] = Field(default_factory=list)


class StatusResult(BaseModel):
    """The stop rule's decision, read only."""

    decision: str
    overrides: list[str] = Field(description="every setting that differs from the defaults")
    details: dict[str, Any]


class ReportResult(BaseModel):
    """The run's report, in markdown."""

    markdown: str


class TemplateRow(BaseModel):
    id: str
    version: int
    source: str
    title: str


class CandidateRow(BaseModel):
    id: str
    kind: str
    template: str | None
    dimension: str | None
    count: int
    rejected_before: list[str]
    commands: list[str] = Field(description="what a person runs to decide; never run them")


class RubricsList(BaseModel):
    """Every template and the open candidates of a library."""

    library: str
    templates: list[TemplateRow]
    candidates: list[CandidateRow]
    warnings: list[str]


class Suggestion(BaseModel):
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


class ShowResult(BaseModel):
    """One template as served, and the open candidates on it."""

    id: str
    version: int
    source: str
    template: dict[str, Any]
    candidates: list[CandidateRow]


class FeedbackResult(BaseModel):
    """Your read recorded as a candidate. A person decides it."""

    candidate_id: str
    path: str
    commands: list[str] = Field(description="what a person runs to decide; never run them")


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


def _decide_commands(lib: Path, cid: str) -> list[str]:
    where = shlex.quote(str(lib))
    return [
        f"sealed-panel rubrics promote {cid} --library {where}",
        f'sealed-panel rubrics reject {cid} --reason "<why>" --library {where}',
    ]


def _candidate_row(lib: Path, c: dict[str, Any]) -> CandidateRow:
    tpl = c.get("template") or {}
    return CandidateRow(
        id=c["id"],
        kind=c["kind"],
        template=f"{tpl.get('id')} v{tpl.get('version')}" if tpl else None,
        dimension=c.get("dimension"),
        count=int(c.get("count") or 0),
        rejected_before=[
            f"v{(b.get('template') or {}).get('version')}: {b.get('reason')}"
            for b in c.get("rejected_before") or []
        ],
        commands=_decide_commands(lib, c["id"]),
    )


def build_server(root: Path | None = None, config: Path | None = None) -> FastMCP:
    """The sealed-panel MCP server. Relative paths resolve against `root` (default: the current
    directory). `config` is the user's configuration: `init` freezes it into every run, and
    `judge` refuses a run whose judge settings differ from it."""
    base = (root or Path.cwd()).expanduser().resolve()
    default_config = config.expanduser().resolve() if config else None

    def path(p: str) -> Path:
        given = Path(p).expanduser()
        return (given if given.is_absolute() else base / given).resolve()

    def opt(p: str | None) -> Path | None:
        return path(p) if p else None

    mcp = FastMCP(
        "sealed-panel",
        instructions=INSTRUCTIONS,
        version=__version__,
        mask_error_details=True,
        strict_input_validation=True,
    )

    def server_config() -> Config:
        """The configuration the user started the server with, read again at every call."""
        return load_config(default_config)

    @mcp.tool(annotations=ADDS)
    def init(
        run: RunPath,
        spec: Annotated[str, Field(description="the spec JSON file")],
        library: LibraryPath = None,
    ) -> InitResult:
        """Validate a spec and freeze it into a new run directory, with the configuration the
        server was started with (its judge command included): a caller never picks its judge."""

        def call() -> InitResult:
            digest = init_run(path(run), path(spec), server_config(), opt(library))
            return InitResult(run=str(path(run)), rubric_sha256=digest)

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def brief(
        run: RunPath,
        round: Annotated[int, Field(description="loop round number, from 1")],
        artefact: Annotated[str, Field(description="the candidate text file")],
        control: Annotated[str, Field(description="the deliberately bad version")],
        good: Annotated[str | None, Field(description="a known-good the user validated")] = None,
    ) -> BriefResult:
        """Write one blind brief per judge for a loop round."""
        paths = _calling(
            lambda: write_briefs(path(run), round, path(artefact), path(control), opt(good), False)
        )
        return BriefResult(
            run=str(path(run)), round=round, sealed=False, briefs=list(map(str, paths))
        )

    @mcp.tool(annotations=ADDS)
    def seal(
        run: RunPath,
        artefact: Annotated[str, Field(description="the final text file")],
        control: Annotated[str, Field(description="the same control as the loop")],
        good: Annotated[str | None, Field(description="a known-good the user validated")] = None,
        round: Annotated[int, Field(description="sealed round number")] = 1,
    ) -> BriefResult:
        """Brief the sealed panel: after CONVERGED, PLATEAU or CAP, or as a one-shot grade."""
        paths = _calling(
            lambda: write_briefs(path(run), round, path(artefact), path(control), opt(good), True)
        )
        return BriefResult(
            run=str(path(run)), round=round, sealed=True, briefs=list(map(str, paths))
        )

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
    ) -> RecordResult:
        """Validate and store one judge's reply: from its transcript, or pasted (flagged)."""

        def call() -> RecordResult:
            given = [x for x in (transcript, reply_file, reply) if x is not None]
            if len(given) != 1:
                raise ToolError("give exactly one of transcript, reply_file or reply")
            text = reply if reply is not None else None
            if reply_file is not None:
                text = path(reply_file).read_text("utf-8")
            rnd = resolve_round(round, sealed)
            r = record_from(path(run), rnd, judge, agent_id, sealed, opt(transcript), text)
            return _record_result(r)

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def void(
        run: RunPath,
        judge: Annotated[int, Field(description="the judge slot")],
        agent_id: Annotated[str, Field(description="the agent that never replied")],
        transcript: Annotated[str, Field(description="its transcript, holding the brief id")],
        round: RoundNo = None,
        sealed: Sealed = False,
    ) -> RecordResult:
        """Record an agent that received a brief and never replied. A void never fills a seat."""

        def call() -> RecordResult:
            rnd = resolve_round(round, sealed)
            r = record_from(path(run), rnd, judge, agent_id, sealed, path(transcript), void=True)
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
        where = path(run)
        expected = _calling(lambda: server_config().judge)
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
            ok = record_measurement(path(run), rnd, id, value, passed, sealed)
            return MeasureResult(id=id, value=value, passed=ok)

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def aggregate(run: RunPath, round: RoundNo = None, sealed: Sealed = False) -> AggregateResult:
        """Aggregate a round once. A loop round writes the author's findings file; a sealed panel
        writes learned rubric candidates to the library inbox."""

        def call() -> AggregateResult:
            rnd = resolve_round(round, sealed)
            e = aggregate_round(path(run), rnd, sealed)
            return AggregateResult(
                round=e["round"],
                sealed=e["sealed"],
                rejected_or_void=len(e["rejected_judges"]) + len(e["voided"]),
                feedback=None if sealed else str(feedback_path(path(run), e["round"])),
                learning=e.get("learning"),
                inbox=list(e.get("inbox") or []),
            )

        return _calling(call)

    @mcp.tool(annotations=READ)
    def status(run: RunPath) -> StatusResult:
        """The stop rule's decision: CONTINUE, CONVERGED, PLATEAU, CAP or SEALED."""
        d = _calling(lambda: run_status(path(run)))
        rest = {k: v for k, v in d.items() if k not in ("decision", "overrides")}
        return StatusResult(decision=d["decision"], overrides=d["overrides"], details=rest)

    @mcp.tool(annotations=READ)
    def report(run: RunPath) -> ReportResult:
        """The run's report: the sealed grade, the round table and every note."""
        return ReportResult(markdown=_calling(lambda: render_report(path(run))))

    @mcp.tool(annotations=READ)
    def rubrics_list(library: LibraryPath = None, run: str | None = None) -> RubricsList:
        """Every rubric template (built-in and user) and the open candidates of the library."""

        def call() -> RubricsList:
            lib = library_for(opt(library), opt(run))
            served, warnings = rubrics_lib.all_templates(lib)
            cands, more = rubrics_lib.open_candidates(lib)
            return RubricsList(
                library=str(lib),
                templates=[
                    TemplateRow(
                        id=tid,
                        version=int(s["template"]["version"]),
                        source=str(s["source"]),
                        title=str(s["template"]["title"]),
                    )
                    for tid, s in served.items()
                ],
                candidates=[_candidate_row(lib, c) for c in cands],
                warnings=warnings + more,
            )

        return _calling(call)

    @mcp.tool(annotations=READ)
    def rubrics_suggest(
        kind: Annotated[str, Field(description="the kind of text, such as email or cv")],
        text: Annotated[
            str | None, Field(description="the text file; its keywords only break ties")
        ] = None,
        top: Annotated[int, Field(ge=1, description="how many templates")] = 3,
        library: LibraryPath = None,
        run: str | None = None,
    ) -> SuggestResult:
        """The rubric templates for a kind of text, with when to use each and its dimensions."""

        def call() -> SuggestResult:
            lib = library_for(opt(library), opt(run))
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

    @mcp.tool(annotations=READ)
    def rubrics_show(
        id: Annotated[str, Field(description="the template id")],
        library: LibraryPath = None,
        run: str | None = None,
    ) -> ShowResult:
        """One template as served (a user template overrides a built-in), with the open
        candidates on it and the command a person runs to decide each."""

        def call() -> ShowResult:
            lib = library_for(opt(library), opt(run))
            served = rubrics_lib.get_template(lib, id)
            t = served["template"]
            cands = [
                c
                for c in rubrics_lib.open_candidates(lib)[0]
                if (c.get("template") or {}).get("id") == id
            ]
            return ShowResult(
                id=id,
                version=int(t["version"]),
                source=str(served["source"]),
                template=dict(t),
                candidates=[_candidate_row(lib, c) for c in cands],
            )

        return _calling(call)

    @mcp.tool(annotations=ADDS)
    def feedback(
        run: RunPath,
        note: Annotated[str, Field(description="where the user's own read differs")],
        dimension: Annotated[
            str | None, Field(description="the dimension; none proposes a new one")
        ] = None,
        library: LibraryPath = None,
    ) -> FeedbackResult:
        """Record the user's read disagreeing with the sealed grade, as a rubric candidate. A
        person decides it with the returned command."""

        def call() -> FeedbackResult:
            lib = library_dir(opt(library)) if library else None
            p = user_disagreement(path(run), note, dimension, lib)
            return FeedbackResult(
                candidate_id=p.stem, path=str(p), commands=_decide_commands(p.parent.parent, p.stem)
            )

        return _calling(call)

    return mcp


def serve(root: Path | None = None, config: Path | None = None) -> None:
    """Serve over stdio until stdin closes. Logs go to stderr; stdout carries the protocol."""
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    build_server(root, config).run(transport="stdio", show_banner=False)
