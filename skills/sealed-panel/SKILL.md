---
name: sealed-panel
description: Grade a text (CV, cover letter, pitch, message, essay, plan, doc, a prompt's output) with blind panels of fresh LLM judges and a sealed final panel, never by its author. Freezes a rubric with 1/3/5 anchors, plants a bad control in every brief, checks every quote by code, loops author and panel until 5 everywhere, a measured plateau or a round cap, then takes the grade from a sealed panel the loop never saw. Use whenever the user asks to grade, score, rate or evaluate a text, review it as a persona, or asks whether it is ready. Not for reviewing code changes.
---

# sealed-panel

The grade is never the author's. You (the agent) orchestrate; the `sealed-panel` command
refuses every step it knows of that would let a grade be re-rolled, or flags it in the report
where it cannot be checked. Print the step you are in.

**The author may:** scope, draft the rubric, write and revise the text, read the findings.
**The author may never:** give the grade, see a judge before it scores, show a judge an earlier
score, change the rubric mid-run, re-roll a panel, or report a number the sealed panel did not
give.

Set `RUN` to a new directory per evaluation (for example `runs/<slug>`). Give the run a neutral
title: judges see it.

**Tools or commands.** When the sealed-panel MCP tools are available, use them: each carries
the name of the command below (`init`, `brief`, `record`, `measure`, `aggregate`, `status`,
`report`, `feedback`, `rubrics_list`, `rubrics_suggest`, `rubrics_show`), with `seal` for
`brief --sealed`, `void` for `record --void`, and `judge` for `sealed-panel judge`. Otherwise
run the `sealed-panel` command. Never promote or reject a rubric candidate yourself, through
any tool or command: show the user the command and let them run it.

## 0. Scope

One line each: the text and its kind; the persona or personas who will read it and what they
decide; the purpose (what a 5 makes happen); the facts the judges may trust (`facts` as text,
or `facts_file` as a path, not both);
the user's declared choices, each with the user's own words in `user_said`; and what an
instrument measures instead of a judge (word count, reading time, a fact check by script),
with a numeric `max` or `min` when there is one. Ask the user only what the conversation does
not already say.

## 1. Rubric

Always start from the library. Run:

```
sealed-panel rubrics suggest --kind <kind of text> --text draft.md
```

and show the user the matching template's `when_to_use` and its table of dimensions (id and
question), so they can say whether it fits. When no template matches, the command lists every
template with its `when_to_use`; say so and write the spec by hand. The skeleton's
`template` reference (`id`, `version`, `sha256`) is checked by `init`, which also records
whether you modified the dimensions; leave it as written. Then write the spec from
the chosen one (`sealed-panel rubrics use ID --out spec.json [--persona-file personas.json]`)
and replace every `TODO(sealed-panel)` marker; `init` refuses the spec until they are gone.
When no template fits, write the spec by hand as below.

Write `spec.json`, starting from `spec.example.json` beside this file; every key is described
in `spec-reference.md`, and unknown keys are refused. Three to six
dimensions per persona, each a question plus anchors at 1, 3 and 5 written as observable
evidence on the page ("names a number the persona can check"), never adjectives
("convincing"). A 5 also means no material or blocking flaw on that dimension. Show the rubric
to the user when the persona or the stakes are theirs to define; otherwise state it in one
table and go.

## 2. Freeze

```
sealed-panel init RUN --spec spec.json
```

Every later command refuses if the rubric or the configuration changed. A new rubric is a new
run. A configuration file, if any, is given here (`init --config FILE`) and nowhere else (the
MCP `init` tool takes none: it freezes the file the server was started with); the report lists
every setting that differs from the defaults.

## 3. The control, and a known-good if one exists

The control is a deliberately bad version: same topic, length and polish, failing the
1-anchors (generic, unsupported, off-purpose). Have a separate agent write it, not the author's
context. Use the same control for the whole run. A known-good is a text the user already
validated (a version they shipped, a reference they named), never one you write; pass it with
`--good`. Without a known-good, say in the report that discrimination was checked downward
only.

## 4. A round

```
sealed-panel brief RUN --round N --artefact draft.md --control control.md [--good good.md]
```

When the run has a judge command (`status` lists `judge.command` among its settings), run

```
sealed-panel judge RUN --round N
```

instead of launching judges: each brief goes to one fresh process, and its reply is recorded
with its proof. Run it again to replace a rejected or void judge, then go to step 5's
instruments and aggregation. Such a run takes runner replies only: `record` is refused on it.
A judge can take a minute and they run one after the other, so through the MCP tool, split a
panel with its `judges` parameter (`judges: [1, 2]`, then `[3, 4]`) to keep each call under your
host's tool timeout. A seat another call holds is skipped, never judged twice.

Otherwise launch all judges at once. Each judge is a **fresh** agent context, with no tools,
whose whole prompt is the brief file's text pasted verbatim, preceded by the instructions in
`judge.md` when your tool supports a system prompt or an agent definition (an `eval-judge`
agent, when your host provides one, carries them). Never reuse a judge, never add your
reasoning, an earlier score, or which item is which. Never use an agent that can read the run
directory.

## 5. Record and aggregate

For each judge, record its reply. Prefer the judge's own transcript when your tool writes one
(a JSONL file whose assistant entries carry text blocks; field names are configurable):

```
sealed-panel record RUN --round N --judge J --agent-id ID --transcript judge.jsonl
```

The transcript's first user entry must contain the whole brief file: paste the brief verbatim,
or `record` refuses it. Text you add around the brief is counted and reported, so add nothing.
Otherwise save the reply to a file, unchanged, and use `--output reply.json`.

Without JSONL transcripts, every reply is pasted: the report flags each round, and the defence
against selective recording (an agent briefed but never recorded blocks the aggregation) does
not apply, because nothing shows which agents received a brief. Say so to the user.

Record every judge you launched, rejected or not; every judge slot needs at least one recorded
attempt before the round can be aggregated. Record calls must be made one after the other. A
judge that received its brief (the brief id appears in its transcript) but stopped without a
reply is recorded as void, so the round can be aggregated:

```
sealed-panel record RUN --round N --judge J --agent-id ID --transcript judge.jsonl --void
```

A void clears the block but never fills the seat: brief a fresh judge on the same brief
and record it under the same `--judge` number. A judge whose prompt held the whole brief and
that left any reply, in any message, cannot be voided: record its reply. Voids have their own
cap of 3 per slot and round. If your agent runtime returns a subagent's answer as a tool call,
set `transcript.handback_tool` in the configuration given to `init`. A REJECTED judge (control
too high, known-good too low, quotes not found, malformed) is replaced by a fresh judge on the
same brief file, recorded under the same `--judge` number.

Instruments, before aggregating:

```
sealed-panel measure RUN --round N --id length --value 142
```

Then, once per round:

```
sealed-panel aggregate RUN --round N
sealed-panel status RUN
```

`aggregate` writes the findings file for the author; `status` only reads.

## 6. Revise or stop

`status` returns one decision.

- **CONTINUE:** read `RUN/feedback/after_rN.md` (the judges' blocking and material reasons, not
  their numbers). Make one idea of change plus a pass that removes what does not serve the
  purpose. Never write the rubric into the text, never fix a cosmetic point for its own sake.
  Go to step 4 with N + 1.
- **CONVERGED**, **PLATEAU** or **CAP:** seal. Nothing else can seal.

## 7. Seal

```
sealed-panel brief RUN --sealed --artefact final.md --control control.md [--good good.md]
```

With `--sealed`, `--round` defaults to 1. Fresh judges (or `judge RUN --sealed`), then
`record ... --sealed`,
`measure ... --sealed` (every measured criterion, before aggregating), `aggregate ... --sealed`,
and:

```
sealed-panel report RUN
```

One sealed panel per run. A one-shot "grade this" is steps 0 to 3, then 7 directly.

## 8. Tell the user

Give the sealed grade per dimension with every judge's score, the verdict (converged, or
below 5 on which dimension), the round-1 baseline against the sealed text, the open findings,
the round table with instruments, the repeat-test line when present, every setting that
differs from the defaults, and how many judge contexts ran. Say whether replies came from
transcripts or were pasted. A text changed after the seal
is unjudged: say so, or start a new run.

Then point the user to the library inbox (`sealed-panel rubrics list --run RUN`): after the
seal, rubric-level candidates are written there (a dimension the sealed judges disagree on, a
dimension where the sealed panel scores the bad control close to a strong text). If aggregate
printed "learning skipped", run `sealed-panel learn RUN`. If the user's own read disagrees with
the grade, record it with `sealed-panel feedback RUN --note "..." [--dimension ID]`. Never
promote a candidate: draft a `proposed_change` only when the user asks for one, and let the user
run `sealed-panel rubrics promote ID` themselves (it shows the change side by side and asks them
to confirm) or `sealed-panel rubrics reject ID --reason "..."`. Candidates quote the judged
texts: the user library is private.

## Refusals

| Situation | What happens |
|---|---|
| The author scores its own text, in any form | Not given. Run the panel, or say no grade exists yet. |
| The rubric or the configuration is edited mid-run | Every command refuses; start a new run. |
| Control above 2 or known-good below 4 (judge's mean) | Judge discarded; brief a replacement on the same brief. |
| A quote not in the item, or a string of tiny fragments | That score is void; more than a third void discards the judge. |
| A second reply for a judge, a reply recorded twice, a late judge, a second aggregation | Refused. |
| Sealing while CONTINUE, a second sealed panel, a loop round after a seal | Refused. |
| An agent briefed but not recorded; a transcript whose prompt is not the whole brief | Aggregation or recording refused. Record a judge that never replied with `--void`. |
| A sealed panel with an instrument not yet measured | Aggregation refused. |
| A judge slot with no real attempt (voids do not count) | Aggregation refused: brief a fresh replacement on the same brief. |
| A configuration value outside its bounds | `init` refuses; every override is printed in the report. |
| Resubmitting a text already judged in the run | Refused. |
| A paired jump of 2 or more on a dimension | Reported as suspect: read the two versions yourself. |
| The candidate or the spec addresses the judge | Flagged (candidate) or refused (spec). |

## Cost

Per persona and round: `judges_per_persona` judge calls (3 by default), plus at most 2
replacements per judge. A one-shot grade is one control writer plus one sealed panel. Say the
expected number of calls before a multi-persona run.
