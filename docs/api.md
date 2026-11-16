# The Python API

The command line and the MCP server call the same functions a library user does. This page
lists the stable ones: the names in `sealed_panel.__all__`. A name not listed here imports from
its module and may change before 1.0.

## A sealed grade in one call

With a judge command in `judge.toml` (see `examples/runner/`), `grade()` freezes a new run from
a spec written in Python, briefs the sealed panel, runs one fresh judge process per brief,
aggregates, and returns the result it wrote to `RUN/grade.json`. `tests/test_api.py` runs it.

```python
from pathlib import Path
from sealed_panel import Spec, grade

anchors = {"1": "no action named", "3": "an action, no date", "5": "an action and a date"}
ask = {"id": "ask", "question": "Does the reader know what to do, by when?", "anchors": anchors}
reader = {"id": "em", "description": "An engineering manager with five minutes"}
spec = Spec("Build cache announcement", "Each team enables the cache", (reader,), (ask,))
result = grade(Path("draft.md"), spec, control=Path("control.md"), config=Path("judge.toml"))
print(result.decision, result.grade["em"]["ask"]["median"], result.path)
```

`grade()` takes the judge from the configuration only, and refuses a configuration without a
judge command: in host mode your agent host runs the judges, and the steps below record them.
Without `run`, the run is a fresh directory under the system's temporary folder, named in
`result.run`; nothing is written to the current directory. It is the one-shot sealed grade, with
no revision loop and no instrument reading (a spec with measured criteria is refused once
frozen: continue that run with the steps). Every refusal of a step is raised as the step raised
it, so a candidate that addresses a judge is refused by the sealed brief before a judge runs.

`result.stands` is True only for a sealed grade that stands. Two outcomes are returned without
one:

| `decision` | When | `path` |
|---|---|---|
| `SEALED` | The sealed panel's grade stands. | `RUN/grade.json` |
| `BLOCKED BY FLAG` | The sealed round carried text addressed to a judge that the spec does not acknowledge; the figures are there and do not stand. | `RUN/grade.json` |
| `INCOMPLETE` | The judges left the panel without a grade: a judge that failed (a void) or too few valid replies. `why` says which; the run stays open, and `sealed-panel judge RUN --sealed` then `aggregate --sealed` finish it. | None |

## A sealed grade step by step

The same grade, one step at a time: this freezes the spec into `runs/announcement`, briefs the
sealed panel, runs one fresh judge process per brief, aggregates and prints the report. It is
the path for host mode (`record_from` in place of `judge_round`) and for a revision loop.
`tests/test_api.py` runs it too.

```python
from pathlib import Path
from sealed_panel import Spec, aggregate_round, init_run, judge_round, load_config
from sealed_panel import render_report, write_briefs

anchors = {"1": "no action named", "3": "an action, no date", "5": "an action and a date"}
ask = {"id": "ask", "question": "Does the reader know what to do, by when?", "anchors": anchors}
reader = {"id": "em", "description": "An engineering manager with five minutes"}
spec = Spec("Build cache announcement", "Each team enables the cache", (reader,), (ask,))
run = Path("runs/announcement")
init_run(run, spec, load_config(Path("judge.toml")))
write_briefs(run, 1, Path("draft.md"), Path("control.md"), None, sealed=True)
judge_round(run, 1, sealed=True)
aggregate_round(run, 1, sealed=True)
print(render_report(run))
```

A loop run is the same calls with `sealed=False` and a round number, revising from
`RUN/feedback/after_rN.md` while `status(run)["decision"]` is `CONTINUE`.
`examples/runner/library_usage.py` replaces a rejected or void judge once.

## Stable names

| Name | What it is |
|---|---|
| `Spec` | A spec written in Python, given to `init_run` in place of a spec file. It goes through the same checks as a file. A key left at None or empty is left out, so the run's defaults apply; facts are given as text. |
| `init_run(run, spec, cfg, library, facts_root, acknowledge)` | Validate a spec (a file or a `Spec`), freeze it and the configuration into a new run, return the rubric hash. |
| `write_briefs(run, rnd, artefact, control, good, sealed)` | Write one blind brief per judge for a round. |
| `record_reply(run, rnd, judge, agent_id, raw, sealed, ...)` | Validate and store one judge's reply, or a void attempt, in host mode. |
| `record_from(run, rnd, judge, agent_id, sealed, transcript, output, void)` | Record one judge from its transcript or a pasted reply, as `record` does. |
| `judge_round(run, rnd, sealed, only, expected, on_seat)` | Run the configured judge command once per open seat of a round. |
| `record_measurement(run, rnd, mid, value, passed, sealed)` | Record one instrument reading. |
| `grade(text, spec, *, control, config, run, known_good)` | Grade a text in one call with the judge runner, and return its `GradeResult`. |
| `GradeResult` | What `grade()` returns: the result file's fields, the run and the file; `GradeResult.read(path)` reads a result file. |
| `aggregate_round(run, rnd, sealed)` | Aggregate a round once and store its result; for the sealed panel, also write `RUN/grade.json`. |
| `status(run)` | The stop rule's decision, read-only. |
| `render_report(run)` | The report, as Markdown. |
| `Config`, `load_config(path)` | The configuration: the packaged defaults, or merged with a TOML file. |
| `SCHEMA_VERSION` | The run format this release reads and writes ([protocol.md](protocol.md), section 11). |
| `__version__` | The package version. |

`GradeResult.read(path)` reads a result file back.

## The result file

The sealed aggregate step writes `RUN/grade.json`, owner-only, whatever called it (`grade()`,
`aggregate_round`, the `aggregate` command or the MCP tool). It is derived from the run's files
by the function the report prints its sealed grade from, so the two agree field by field.
`GradeResult` has the same fields, plus `run` and `path`. Its format is `schema_version` 1,
separate from the run format: a reader accepts every result format up to its own and refuses a
later one with the advice to upgrade ([protocol.md](protocol.md), section 11).

| Key | Type | What it holds |
|---|---|---|
| `schema_version` | int | The result file's format. |
| `sealed_panel` | str | The release that wrote the file, from the installed distribution. |
| `run_schema_version` | int | The run's format (`run.json`). |
| `title` | str | The spec's title. |
| `rubric_sha256`, `config_sha256` | str | The rubric hash and the configuration's fingerprint frozen at `init`. |
| `template` | object or null | The template the spec came from: `id`, `version`, `sha256`, `modified`. |
| `candidate_hash` | str or null | The sha256 of the graded text, normalised. |
| `decision`, `stands`, `why` | str, bool, str | `SEALED` or `BLOCKED BY FLAG`; whether the grade stands; why not, or empty. |
| `verdict` | str | What the grade says, in the report's words. |
| `grade` | object | Per persona, per dimension: `median`, `scores`, `severities`, `sd`, and `baseline_median` (the round-1 text, or null). |
| `obtained` | object | `loop_rounds` and `loop_decision` (the stop decision before the seal, null for a one-shot grade), `seats`, the panel's `composite` (for information) and `noise`, and its `measured` readings. |
| `findings` | object | Per persona, the blocking and material reasons the judges gave (`judge`, `dimension`, `severity`, `text`). |
| `judge` | object | `mode` (`runner` or `host`), `argv` (the frozen judge command, empty in host mode), `model_id` (the declared one, or empty), `transcript_format` (host mode, else null). |
| `flags` | object | `injection` (flagged patterns by item), `injection_acknowledged`, `pasted_replies`, `judge_env_changed` (between the panel's attempts), `panel_env_changed` (between the panel and the loop), `prompt_extras`, `one_quote_judges`. |
| `attempts` | object | The sealed panel's `valid`, `rejected` and `void` attempts, and their `sources`. |
| `settings` | list of str | Every setting that differs from the defaults, as the report lists them. |

`flags.prompt_extras` holds, for each seat whose counted judge was read from a transcript, the
characters its prompt carried beyond its brief, zero included, as its format's adapter counted
them. The two formats do not count the host's own context the same way
([integrations.md](integrations.md)): read it with `judge.transcript_format`.

Every argument is documented in the function's docstring. The records the steps return
(`Attempt`, `RoundResult`, `Decision`) are typed in `sealed_panel.model` as `TypedDict`s:
plain dicts at run time, the same JSON a run stores. The package ships `py.typed`, so a type
checker reads them.

## Exceptions

| Exception | When |
|---|---|
| `EvalError` | Every refusal: a step the protocol refuses, or an input it cannot use. The others below are `EvalError`s too. |
| `ConfigError` | A configuration file that does not fit the defaults' shape or bounds. |
| `SeatTaken` | A seat another call holds right now, or one that already holds a valid reply. `judge_round` skips such a seat; a caller of `record_reply` may try again later. |
| `FormatError` | A transcript whose structure its format's adapter does not recognise: refused, never guessed. |
| `MalformedReply` | A reply a transcript holds in a shape that cannot be read. `record_from` records it as rejected; a caller of `transcript.last_reply` gets it. |

A file that is missing or unreadable raises `OSError`.

## Unstable until 1.0

These import from their modules and may change without a major version:

- the validators and readers: `rubric.validate_spec`, `reply.check_output`,
  `transcript.last_reply`, `text.normalise`, `text.quote_found`, `decide.decide`;
- the pieces of a step: `brief.build_brief`, `runner.open_seats`, `runner.judge_seat`,
  `run.load_frozen`, `result.overrides`, `settings.default_config`;
- the record types in `sealed_panel.model` other than `Spec`;
- everything under `sealed_panel.learning`.
