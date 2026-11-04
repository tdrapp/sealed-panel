# sealed-panel

[![CI](https://github.com/tdrapp/sealed-panel/actions/workflows/ci.yml/badge.svg)](https://github.com/tdrapp/sealed-panel/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

Grade a piece of writing with blind panels of LLM judges, then let a sealed panel give the final grade.

## What it is

An author who grades their own work is generous to it, and that holds for an agent revising a
text as much as for a person. Handing the grade to an LLM judge helps only partly: a judge sees
the scores it gave before, learns which version is the newest, drifts toward the author's
framing, and can be asked again until it says what the author hoped.

sealed-panel is the deterministic half of an evaluation loop. It freezes a rubric, writes blind
briefs for a fresh panel of judges every round, checks each judge's reply by code, applies a
stop rule, and takes the grade from a sealed panel that never saw the loop. The calling agent
(or person) orchestrates. Every re-roll path through the CLI that this version knows of is
refused, or flagged in the report where it cannot be checked. The threat model is an agent
under optimisation pressure driving the CLI to get a better grade, not a person editing the
run's files by hand.

The test suite is checked by mutation: `tools/mutation_check.py` switches off one refusal,
bound or boundary at a time and requires the suite to fail on each. Of its 133 mutations, 131
are killed; the two that survive are redundant guards, kept as defence in depth, listed in the
tool with the guard that makes each redundant, and not counted as tested. CI runs it on every
pull request.

It is a Python package with no runtime dependencies and a CLI, plus an agent skill that drives
it.

## Use cases

Anything a reader judges and you can describe with a persona and a purpose: CVs and cover
letters, pitches, emails and messages, essays, plans and design docs, and the outputs of a
prompt you are tuning.

## When not to use it

It is not a code review tool: a diff needs tests and a reviewer who runs the code. It is not a
benchmark harness either: for a dataset of thousands of items, use an evaluation framework
built for statistics over many samples. sealed-panel is for one text at a time, where the
only repetitions are the judges.

## Key principles

The author never grades. The author drafts the rubric and revises the text; the grade comes
from judges who did not write it, and the author reads their findings, not their numbers.

Every brief goes to a new judge context. Item labels and item order are shuffled per judge,
reading stances rotate, and no brief carries a score, a round number, or which item is the
candidate. A deliberately bad version, the control, rides in every brief: a judge who scores it
above 2 on average is discarded. An optional known-good text, validated by the user, checks the
other side.

The rubric is anchored and frozen. Each dimension has anchors at 1, 3 and 5 written as evidence
on the page. The spec, its facts and the configuration are hashed at `init`, and any later
change stops every command. Settings that guard the protocol have hard bounds, and the report
lists every setting that differs from the defaults.

Each score must cite the item verbatim, and the quote is checked by code: a quote that is not in
the item voids the score, and more than a third of void quotes discard the judge. Each score
also carries the severity of the worst flaw on that dimension. A blocking flaw caps it at 3, a
material flaw at 4, so a 5 means no material flaw.

The loop stops on one of three conditions. CONVERGED: every median is 5 and every instrument
passed. PLATEAU: two rounds running, the best paired gain was below the noise. Paired gains
are medians of integer differences and move in steps of 0.5, so with the noise between its 0.5
floor and 1 this means no dimension gained a full point, two rounds running. CAP: the round
limit was reached. Then a sealed panel of fresh judges the loop never fed gives the grade, once
per run.

## How a run works

```
 spec.json --> init (validate, freeze, hash)
                 |
                 v
         +--> brief round N ------> one blind brief per judge
         |        |                 (shuffled labels and order, control planted)
         |        v
         |   judges reply --------> record (whole brief, quotes, severity, control)
         |        |
         |        v
         |   aggregate round N ---> medians, paired gain, noise, findings file
         |        |
         |        v
         |     status
         |      |    |
         | CONTINUE  CONVERGED / PLATEAU / CAP
         |      |                 |
         +-- author revises       v
             from findings     brief --sealed --> fresh judges --> record --> aggregate
                                                                                |
                                                                                v
                                                                             report
```

## Install

sealed-panel needs Python 3.11 or later and has no runtime dependencies. It is not on PyPI yet;
install it from GitHub.

With pipx:

```sh
pipx install git+https://github.com/tdrapp/sealed-panel
```

With pip, in a virtual environment:

```sh
pip install git+https://github.com/tdrapp/sealed-panel
```

From source, with uv:

```sh
git clone https://github.com/tdrapp/sealed-panel
cd sealed-panel
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
source .venv/bin/activate
sealed-panel --version
```

## Quick start

This walks the synthetic example in `examples/announcement`: an internal email announcing a
build cache, graded for a busy engineering manager. Run it from a clone of the repository, so
the example files are at hand. The example spec sets `max_rounds` to 1 and
`judges_per_persona` to 2 to keep the walk short; the defaults are 6 and 3, and the report
lists both as settings that differ from the defaults.

**1. Freeze the rubric.**

```sh
sealed-panel init runs/announcement --spec examples/announcement/spec.json
```

```
frozen: rubric sha256 0d16bd433b43c40589650a77440c124bd3a50c236a09198dd824ff4dc11bd5ca
```

**2. Brief round 1.** One brief per judge, the control planted in each.

```sh
sealed-panel brief runs/announcement --round 1 \
  --artefact examples/announcement/draft_v1.md \
  --control examples/announcement/control.md
```

```
runs/announcement/rounds/r1/brief_j1.md
runs/announcement/rounds/r1/brief_j2.md
```

Each brief names the persona, the reading stance, the facts, the rubric with its anchors, and
two items under random labels. It ends with the JSON shape to reply with.

**3. Get one reply per brief.** In a real run, give each brief, verbatim and nothing else, to a
fresh judge: a new agent context with no tools, or a colleague who has not seen the text. Save
each reply to a file.

To walk through the tool without a model, the example ships a demo script that plays the
judges with fixed scores. Its replies say nothing about the text; never record them in a real
evaluation.

```sh
python examples/announcement/play_judges.py runs/announcement --round 1 --out replies
```

```
replies/r1_j1.json
replies/r1_j2.json
```

You can also play the judge by hand: read `brief_j1.md` and write the JSON it asks for. Labels
are drawn again for every brief, so check them each time
(`grep -A2 '<item label=' runs/announcement/rounds/r1/brief_j1.md`). A reply looks like this,
shortened to one item and one dimension:

```json
{
  "items": {
    "G": {
      "retell": "The build cache is live; enable it on one service before 22 November.",
      "dimensions": {
        "ask": {
          "reasoning": "Action, date and effort are all stated.",
          "quote": "enable it on one service before 22 November",
          "severity": "none",
          "score": 5
        }
      },
      "weakest_point": {
        "text": "The release-train risk is only implied.",
        "dimension": "relevance",
        "quote": "restores the old behaviour for one build"
      }
    }
  }
}
```

**4. Record each reply.** `--agent-id` names the judge context; it must be new for every reply
in the run. With `--output`, the reply is read from a file (or `-` for stdin). When your agent
tool writes a JSONL transcript per judge, use `--transcript` instead: the reply is read there,
the transcript's prompt must contain the whole brief, and the report does not flag the reply as
pasted. Some agent runtimes return a subagent's answer as a tool call rather than as a final
text; name that tool in `transcript.handback_tool` and its message is read as the reply. An
agent that received a brief and never replied is recorded with `--transcript FILE --void`. A
void clears the block an unrecorded agent puts on the round, but never fills a seat: every
judge slot needs a real attempt (a reply, valid or rejected) before the round can be
aggregated, so brief a fresh replacement on the same brief.

```sh
sealed-panel record runs/announcement --round 1 --judge 1 --agent-id judge-a --output replies/r1_j1.json
sealed-panel record runs/announcement --round 1 --judge 2 --agent-id judge-b --output replies/r1_j2.json
```

```
VALID judge 1
VALID judge 2
```

A rejected reply prints `REJECTED` and the reason (a quote not in the item, the control scored
above 2, a missing dimension). Brief a fresh judge on the same brief file and record it under
the same `--judge` number.

**5. Measure what an instrument measures, then aggregate.**

```sh
sealed-panel measure runs/announcement --round 1 --id length \
  --value "$(wc -w < examples/announcement/draft_v1.md | tr -d ' ')"
sealed-panel aggregate runs/announcement --round 1
sealed-panel status runs/announcement
```

`aggregate` writes `runs/announcement/feedback/after_r1.md`: the judges' blocking and material
findings, without their numbers, for the author's next revision. `status` reads the ledger and
prints the decision as JSON, with the settings that differ from the defaults. With `max_rounds`
at 1 it is `CONVERGED` if every median is 5 and the length passed, and `CAP` otherwise.

**6. Seal.** Fresh judges, new agent ids, the same control. With `--sealed`, `--round`
defaults to 1.

```sh
sealed-panel brief runs/announcement --sealed \
  --artefact examples/announcement/draft_v1.md \
  --control examples/announcement/control.md
python examples/announcement/play_judges.py runs/announcement --sealed --out replies
sealed-panel record runs/announcement --sealed --judge 1 --agent-id judge-c --output replies/s1_j1.json
sealed-panel record runs/announcement --sealed --judge 2 --agent-id judge-d --output replies/s1_j2.json
sealed-panel measure runs/announcement --sealed --id length \
  --value "$(wc -w < examples/announcement/draft_v1.md | tr -d ' ')"
sealed-panel aggregate runs/announcement --sealed
sealed-panel report runs/announcement
```

The report starts with the sealed grade, every judge's score per dimension, and the verdict:

```
# Evaluation: Build cache announcement email

Rubric sha256 `0d16bd433b43`, decision **SEALED**, 4 judge contexts, settings that differ from the defaults: spec.judges_per_persona = 2 (default 3); spec.max_rounds = 1 (default 6).

**Grade (sealed panel): ask 5 (5/5); relevance 4 (4/4); evidence 5 (5/5). Verdict: below 5 on em/relevance.**
```

Then come the round table, the open findings, the repeat-test line when the sealed text was
also judged in the loop (a measure of panel-to-panel noise), and a note that replies were
pasted rather than read from transcripts.

## Using the agent skill

`skill/` holds an agent skill in the open Agent Skills format: `SKILL.md` is the procedure an
agent follows, `judge.md` holds the instructions for each judge, and `spec.example.json` with
`spec-reference.md` describe the spec. Copy the folder into your agent tool's skills
directory, install the package so the `sealed-panel` command is on the agent's path, and ask the
agent to grade a text. Run judges as fresh contexts with no tools, and paste each brief as their
whole prompt.

## Configuration

The settings live in [`src/sealed_panel/defaults.toml`](src/sealed_panel/defaults.toml): run
defaults, limits, severity caps, quote rules, text normalisation, injection patterns and
transcript field names. A spec may override the per-run keys; `init --config PATH` overrides
the rest, within hard bounds, and the effective configuration is frozen into the run. The
brief texts stay in code, because they are the instrument. Every key and bound is documented
in [docs/configuration.md](docs/configuration.md). The CLI is described in
[docs/cli.md](docs/cli.md) and the reasoning behind each rule in
[docs/protocol.md](docs/protocol.md).

## Repository structure

```
sealed-panel/
  src/sealed_panel/
    __init__.py          public API
    __main__.py          python -m sealed_panel
    defaults.toml        the settings, with their bounds checked in config.py
    config.py            load, merge and bound the settings; list overrides
    errors.py            EvalError and ConfigError
    store.py             JSON and text files, round directories
    spec.py              validate, freeze and hash the spec
    text.py              normalisation, quote checks, injection flags
    brief.py             blind briefs (the brief texts live here)
    record.py            judge replies, transcripts, handbacks, void attempts, instruments
    ledger.py            views derived from the run's files
    aggregate.py         medians, paired gains, noise, findings
    decide.py            the stop rule
    report.py            findings file, status, report
    cli.py               the sealed-panel command
    py.typed             typing marker
  tests/                 the behaviour contract (pytest), one file per concern
  tools/
    mutation_check.py    seeded defects the tests must catch
  skill/
    SKILL.md             the procedure an agent follows
    judge.md             the judge instructions
    spec.example.json    a spec to start from
    spec-reference.md    every spec key
  examples/announcement/
    spec.json            the example rubric
    facts.md             the facts the judges may trust
    draft_v1.md          the candidate
    control.md           the deliberately bad version
    play_judges.py       demo judges with fixed scores, for the walk-through only
    README.md            the example explained
  docs/
    configuration.md     every setting and its bounds
    cli.md               every command and flag
    protocol.md          why each rule exists
  .github/
    workflows/ci.yml     lint, types, tests on 3.11 to 3.13, mutation check
    branch-protection.json  required checks for main
    dependabot.yml, CODEOWNERS, PULL_REQUEST_TEMPLATE.md, ISSUE_TEMPLATE/
  pyproject.toml, .pre-commit-config.yaml, .editorconfig
  README.md, CONTRIBUTING.md, SECURITY.md, CHANGELOG.md, LICENSE, NOTICE
```

## Development

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pre-commit install
.venv/bin/pre-commit run --all-files
.venv/bin/pytest
.venv/bin/python tools/mutation_check.py
```

`pre-commit install` sets up two stages: lint, format and types on commit; the test suite on
push. CI runs the same checks on Python 3.11, 3.12 and 3.13. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## Limits

The method is not validated against human graders. Judges are models, not the persona; when
your own read and the panel disagree, the rubric is probably missing a dimension, which belongs
in a new run. Judges of one model family also correlate: rotating stances lowers the
correlation and judges from different families lower it more, but the control and the
known-good only catch a judge that does not discriminate, not a blind spot every judge shares.

A verified quote proves the judge saw the text, not that the text supports the score. The
report flags a judge that quotes the same words on every dimension, which is the weakest form
of this. Replacing rejected judges selects the panel: the judges who remain are the ones that
scored the control low and the known-good high. That is the point of calibration, and it can
also shift the panel.
Whether the planted control lifts the candidate by contrast is unmeasured; runs with and
without it would measure it.

The refusals hold for the CLI used as documented. They do not hold against someone who starts
a new run directory, edits the spec and its lock together, or deletes a transcript before
aggregating. Pasted replies (`--output`) carry no proof that the judge received the brief
verbatim, or that no other judge was asked and discarded; the report flags such rounds.
`record` calls for one round must be made one after the other, not in parallel, because each
call reads the files the previous one wrote.

A configuration may still change, within bounds and with disclosure in the report header and in
`status`: the reading stances, the judges per persona (2 to 9), the round limit, the control and
known-good limits, the noise floor, the severity caps (tighter only), the quote lengths and
bad-quote share (stricter only: the floors are the defaults), the punctuation normalisation for
quote matching (one punctuation character to another, never deleting a character, reaching a
letter, digit or whitespace, or adding an ellipsis; the stripped characters are held at the
default set; every other check uses the built-in normalisation), added injection patterns (the
defaults always stay), and the transcript field names. Every such value is frozen into the run
at `init`.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
