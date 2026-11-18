"""The steps a command line, the MCP server and the library API call: each one composes
the domain modules, so none of them needs another's knowledge."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from . import templates
from .aggregate import aggregate
from .brief import write_briefs
from .errors import EvalError, PanelIncomplete
from .ledger import results_by_judge
from .model import RESULT_FILE, RoundResult, Spec
from .record import round_keys
from .report import write_feedback
from .result import GradeResult, write_result
from .run import freeze, load_frozen, prepare_spec, run_config
from .runner import every_seat, judge_round
from .settings import Config, load_config, resolve
from .store import dump_json, load_json, round_dir, run_lock

# Characters of the text file's name kept in a temporary run's name.
SLUG_CHARS = 24
NO_RUNNER = (
    "grade() runs the judge command of its configuration ([judge] command), and this "
    "configuration has none: grade() needs the judge runner. In host mode your agent host runs "
    "the judges: use the steps instead (init_run, write_briefs with sealed=True, record_from for "
    "each judge, aggregate_round), or the `brief`, `record` and `aggregate` commands"
)
MEASURED = (
    "the spec has measured criteria, and grade() takes no instrument readings: grade it with "
    "the steps (init_run, write_briefs with sealed=True, judge_round, record_measurement for "
    "each criterion, aggregate_round), or the `init`, `brief --sealed`, `judge`, `measure` and "
    "`aggregate --sealed` commands"
)


def init_run(
    run: Path,
    spec_path: Path | Spec,
    cfg: Config | None = None,
    library: Path | None = None,
    facts_root: Path | None = None,
    acknowledge: bool = True,
) -> str:
    """Validate the spec, freeze it and the configuration into `run`, return the rubric hash.
    With `facts_root`, a `facts_file` must resolve under it (the MCP server's root).

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file, or a Spec built in Python.
        cfg: The configuration; the packaged defaults when None.
        library: The user rubric library, read for a template the spec names; the default
            one when None.
        facts_root: A folder a `facts_file` must resolve under, or None.
        acknowledge: Whether this caller may freeze a spec that acknowledges injection flags:
            a person at the command line may, the MCP server's caller (the author) may not.
    """
    cfg = resolve(cfg)
    spec = prepare_spec(run, spec_path, cfg, facts_root, acknowledge)
    if isinstance(spec.get("template"), dict):
        templates.check_template_ref(spec, templates.library_dir(library))
    return freeze(run, spec, cfg)


def aggregate_round(run: Path, rnd: int, sealed: bool) -> RoundResult:
    """Aggregate a round once, write it to the round's result.json and return it. After a
    loop round, also write the author's findings to RUN/feedback/after_rN.md; after the sealed
    panel, RUN/grade.json. Called again on a sealed round whose RUN/grade.json is missing (its
    write failed), it writes that file from the round files and returns the stored result; it
    never rewrites one, and refuses every other second call. Nothing is written outside the
    run: learning is a command of its own. Every seat of the round, then the run's lock, is
    held for the whole step, so no judge starts or records meanwhile; a seat another call holds
    refuses the step.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
    """
    with every_seat(run, rnd, sealed), run_lock(run):  # seats first, then the run
        return _aggregate_held(run, rnd, sealed)


def _aggregate_held(run: Path, rnd: int, sealed: bool) -> RoundResult:
    """aggregate_round, with every seat held: aggregate the round, store its result, and after
    a loop round write the author's findings. A sealed round already aggregated whose result
    file is missing gets that file, and nothing else."""
    spec = load_frozen(run)
    cfg = run_config(run)
    rdir = round_dir(run, rnd, sealed)
    if sealed and (rdir / "result.json").is_file() and not (run / RESULT_FILE).exists():
        # The round's result was stored and the result file's write failed: written again,
        # from the round files, never over an existing one.
        write_result(run)
        stored: RoundResult = load_json(rdir / "result.json")
        return stored
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: a round is aggregated once")
    keys = round_keys(rdir, rnd)
    by_judge = results_by_judge(rdir)
    entry = aggregate(spec, cfg, rdir, keys, by_judge, rnd, sealed)
    dump_json(rdir / "result.json", entry)
    if sealed:  # derived from the round files, so written after the round's own result
        write_result(run)
    if not sealed:
        write_feedback(run, entry)
    return entry


def resolve_round(rnd: int | None, sealed: bool) -> int:
    """The round asked for; with no number, the sealed panel's round 1, never a loop round."""
    if rnd is not None:
        return int(rnd)
    if sealed:
        return 1
    raise EvalError(
        "the round number is required for a loop round (it defaults to 1 only for the sealed panel)"
    )


def _slug(text: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.stem.lower()).strip("-")[:SLUG_CHARS] or "text"


def _fresh_folder(slug: str) -> Path:
    """A new, empty, owner-only folder under the system's temporary folder, never the current
    directory. The run is a new directory inside it, so no other run sits beside it."""
    return Path(tempfile.mkdtemp(prefix=f"sealed-panel-{slug}-"))


def _has_measured(spec: Path | Spec) -> bool:
    """Whether a spec names measured criteria, read before anything is frozen; a spec that
    cannot be read this way is left to init_run, which refuses it with its reasons."""
    data = spec.to_json() if isinstance(spec, Spec) else load_json(spec)
    return isinstance(data, dict) and bool(data.get("measured"))


def _brief_new_run(
    run: Path,
    made: Path | None,
    spec: Path | Spec,
    cfg: Config,
    texts: tuple[Path, Path, Path | None],
) -> None:
    """Freeze the run and brief its sealed panel. A refusal here comes before judging started:
    the folder grade() made (`made`) is removed with what it holds; a caller's run is never
    removed. What stopped the call is what the caller gets: a removal that fails is added to a
    refusal's message, and to any other error as a note, never raised in its place."""
    text, control, known_good = texts
    try:
        init_run(run, spec, cfg)
        write_briefs(run, 1, text, control, known_good, sealed=True)
    except BaseException as exc:
        if made is not None and not _removed(made):  # only grade()'s folder, never a caller's
            left = f"could not remove {made}"
            if isinstance(exc, EvalError):  # the package's own errors take one message
                raise type(exc)(f"{exc}; {left}") from exc
            exc.add_note(left)
        raise


def _removed(folder: Path) -> bool:
    """Remove a folder grade() made, with what it holds; False when that fails."""
    try:
        shutil.rmtree(folder)
    except OSError:
        return False
    return True


def _judge_and_aggregate(run: Path, made: Path | None) -> GradeResult:
    """Run the judges and aggregate. From here on a judge may have run, so the run is kept: a
    refusal in a run grade() made names it, one in a caller's run is raised unchanged."""
    try:
        judge_round(run, 1, True)
        aggregate_round(run, 1, True)
    except PanelIncomplete as exc:
        return GradeResult.incomplete(run, str(exc))
    except EvalError as exc:
        if made is None:
            raise
        raise type(exc)(f"{exc}; the run is kept at {run}") from exc
    return GradeResult.read(run / RESULT_FILE)


def grade(
    text: Path,
    spec: Path | Spec,
    *,
    control: Path,
    config: Config | Path,
    run: Path | None = None,
    known_good: Path | None = None,
) -> GradeResult:
    """Grade a text in one call with the judge runner: freeze a new run, brief the sealed
    panel, run the configured judge command once for every seat, aggregate, and return the
    result the aggregate step wrote to RUN/grade.json. It is the one-shot sealed grade: no
    revision loop, no instrument reading. Every refusal of a step is raised as the step raised
    it. A sealed panel blocked by a flag is returned with `stands` False; one its judges left
    without a grade (a judge that failed, or too few valid replies) is returned as INCOMPLETE,
    with no result file, and its run stays open for a replacement (`judge`, then `aggregate
    --sealed`).

    Without `run`, the run is a new directory inside a folder grade() makes under the system's
    temporary folder. A refusal before judging started removes that folder; once it started, the
    run is kept and the outcome or the error names it. A caller's `run` is never removed.

    Args:
        text: The text to grade.
        spec: The spec file, or a Spec built in Python.
        control: The deliberately bad version planted in every brief.
        config: The configuration, or its TOML file; its `[judge] command` runs the judges.
            Required: a configuration without a judge command is refused.
        run: A new or empty directory for the run; when None, a fresh one under the system's
            temporary folder, named in the result's `run`.
        known_good: An optional known-good text, validated by the user.
    """
    cfg = load_config(config) if isinstance(config, Path) else config
    if not cfg.judge["command"]:
        raise EvalError(NO_RUNNER)
    if _has_measured(spec):
        raise EvalError(MEASURED)
    made, target = None, run
    if target is None:
        made = _fresh_folder(_slug(text))
        target = made / _slug(text)
    _brief_new_run(target, made, spec, cfg, (text, control, known_good))
    return _judge_and_aggregate(target, made)
