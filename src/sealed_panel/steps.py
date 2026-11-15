"""The steps a command line, the MCP server and the library API call: each one composes
the domain modules, so none of them needs another's knowledge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import templates
from .aggregate import aggregate
from .errors import EvalError
from .ledger import results_by_judge
from .record import round_keys
from .report import write_feedback
from .run import freeze, load_frozen, prepare_spec, run_config
from .runner import every_seat
from .settings import Config, resolve
from .store import dump_json, round_dir, run_lock


def init_run(
    run: Path,
    spec_path: Path,
    cfg: Config | None = None,
    library: Path | None = None,
    facts_root: Path | None = None,
    acknowledge: bool = True,
) -> str:
    """Validate the spec, freeze it and the configuration into `run`, return the rubric hash.
    With `facts_root`, a `facts_file` must resolve under it (the MCP server's root).

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file.
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


def aggregate_round(run: Path, rnd: int, sealed: bool) -> dict[str, Any]:
    """Aggregate a round once, write it to the round's result.json and return it. After a
    loop round, also write the author's findings to RUN/feedback/after_rN.md. Nothing is
    written outside the run: learning is a command of its own. Every
    seat of the round, then the run's lock, is held for the whole step, so no judge starts or
    records meanwhile; a seat another call holds refuses the step.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
    """
    with every_seat(run, rnd, sealed), run_lock(run):  # seats first, then the run
        return _aggregate_held(run, rnd, sealed)


def _aggregate_held(run: Path, rnd: int, sealed: bool) -> dict[str, Any]:
    """aggregate_round, with every seat held: aggregate the round, store its result, and after
    a loop round write the author's findings."""
    spec = load_frozen(run)
    cfg = run_config(run)
    rdir = round_dir(run, rnd, sealed)
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: a round is aggregated once")
    keys = round_keys(rdir, rnd)
    by_judge = results_by_judge(rdir)
    entry = aggregate(spec, cfg, rdir, keys, by_judge, rnd, sealed)
    dump_json(rdir / "result.json", entry)
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
