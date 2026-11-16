# The Python API

The command line and the MCP server call the same functions a library user does. This page
lists the stable ones: the names in `sealed_panel.__all__`. A name not listed here imports from
its module and may change before 1.0.

## A sealed grade in a few lines

With a judge command in `judge.toml` (see `examples/runner/`), this freezes a spec written in
Python, briefs the sealed panel, runs one fresh judge process per brief, aggregates and prints
the report. `tests/test_api.py` runs it.

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
| `aggregate_round(run, rnd, sealed)` | Aggregate a round once and store its result. |
| `status(run)` | The stop rule's decision, read-only. |
| `render_report(run)` | The report, as Markdown. |
| `Config`, `load_config(path)` | The configuration: the packaged defaults, or merged with a TOML file. |
| `SCHEMA_VERSION` | The run format this release reads and writes ([protocol.md](protocol.md), section 11). |
| `__version__` | The package version. |

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
| `MalformedReply` | A reply a transcript holds in a shape that cannot be read. `record_from` records it as rejected; a caller of `transcript.last_reply` gets it. |

A file that is missing or unreadable raises `OSError`.

## Unstable until 1.0

These import from their modules and may change without a major version:

- the validators and readers: `rubric.validate_spec`, `reply.check_output`,
  `transcript.last_reply`, `text.normalise`, `text.quote_found`, `decide.decide`;
- the pieces of a step: `brief.build_brief`, `runner.open_seats`, `runner.judge_seat`,
  `run.load_frozen`, `report.overrides`, `settings.default_config`;
- the record types in `sealed_panel.model` other than `Spec`;
- everything under `sealed_panel.learning`.
