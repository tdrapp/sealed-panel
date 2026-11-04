# Command line

```
sealed-panel [--version] <command> RUN [options]
```

`RUN` is the run directory. Only `init` accepts `--config PATH` (see
[configuration.md](configuration.md)); every later command reads the configuration frozen into
the run.

`--round N` is required for a loop round. With `--sealed` it is optional and defaults to 1,
since a run has one sealed panel.

Every refusal prints `sealed-panel: <reason>` on stderr and exits with status 2. So does
malformed input (a file that is not JSON, a spec or run file of the wrong shape): the message
names the problem, never a traceback. A rejected judge reply is not a refusal: `record` stores
it, prints `REJECTED`, and exits 0.

`record` calls for one round must run one after the other, not in parallel: each call reads
the files the previous one wrote.

## init

```
sealed-panel init RUN --spec SPEC.json [--config CONFIG.toml]
```

Validates the spec's shape and values, fills the per-run defaults, reads `facts_file` by
content, and refuses a spec whose own text addresses the judges. Writes `spec.json`,
`rubric.lock` (sha256 of the rubric), `config.json`, `config.lock` and an empty `ledger.json`.
Refuses if `RUN` is already initialised: a new rubric is a new run.

A spec has `title`, `purpose`, `personas` (each with `id` and `description`) and `dimensions`
(each with `id`, `question`, `anchors` at `"1"`, `"3"` and `"5"`, and optionally `name`,
`weight`, the anchors `"2"` and `"4"`, and `personas` to restrict it). Optional: `facts` (text)
or `facts_file` (a path to a file that must exist, read and frozen by content; not both),
`measured` (each with `id`, `description`, and an optional numeric `max` or `min`),
`declared_choices` (each with `choice` and `user_said`, the words of the person who asked for
it), and the keys of the `[run]` configuration table, within their bounds. Unknown keys are
refused at every level. Every `measured` field is type-checked (`id` and `description`
strings, `min` and `max` finite numbers with `min` not above `max`, unique ids), weights must
be finite numbers above 0, and `judges_per_persona` is between 2 and 9. A relative
`facts_file` is read from the spec file's folder. A `facts` text is refused as a path, with a
pointer to `facts_file`, when it starts with `~/`, `./` or `../`; when it starts with `/` and
holds another slash; when it is one token with a slash; or when it is one line ending in a
file extension. Anything else is text, so "~2/3 of revenue" or "/ marks a break" stays facts.
See
`skill/spec-reference.md` and `examples/announcement/spec.json`.

Prints: `frozen: rubric sha256 <hash>`.

## brief

```
sealed-panel brief RUN --round N --artefact FILE --control FILE [--good FILE] [--sealed]
```

Writes `brief_j<J>.md` for each judge (personas times `judges_per_persona`) and `keys.json`
(labels, order, stance and brief id per judge) in `RUN/rounds/rN`, or `RUN/sealed/rN` with
`--sealed`. Prints the brief paths.

| Option | Meaning |
|---|---|
| `--round N` | Round number, from 1. A loop round N needs round N-1 aggregated. |
| `--artefact FILE` | The candidate text. |
| `--control FILE` | The deliberately bad version, planted in every brief. Use the same one for the whole run. |
| `--good FILE` | A known-good text the user validated. A judge who scores it below `good_min` is discarded. |
| `--sealed` | Brief the sealed panel. Allowed only after CONVERGED, PLATEAU or CAP, or before any loop round (a one-shot grade). Once per run. |

From round 2, the previous round's text rides along as a blind paired comparator. In a sealed
panel, the round-1 text rides along as a blind baseline when it differs from the candidate.

Refused: a round number below 1, a round already briefed, a text already judged in this run, a
round past `max_rounds`, a loop round after the loop stopped or after a seal, a second sealed
panel, a control identical to the candidate, a text containing the item tag.

## record

```
sealed-panel record RUN --round N --judge J --agent-id ID (--transcript FILE | --output FILE|-) [--void] [--sealed]
```

Validates one judge's reply and stores it as `out_j<J>_<id>_<hash>.json`. Prints
`VALID judge J`, `REJECTED judge J: <reasons>` or `VOID judge J: ...`.

| Option | Meaning |
|---|---|
| `--judge J` | The judge slot, as numbered in the brief file name. |
| `--agent-id ID` | The judge context. Must be new in this run. |
| `--transcript FILE` | A JSONL transcript of the judge. The reply is the message of its last handback tool call when `transcript.handback_tool` is set and the agent handed back (a malformed one is recorded as rejected), else its last assistant text, else (nothing delivered at all) its last refused handback. A handback the runtime refused (its tool result is an error) was never delivered and is skipped as a reply; for `--void` it still counts as a reply, since the judge answered. The transcript's first user entry must contain the whole brief file, normalised with the built-in settings; characters beyond the brief are counted, stored, and reported. When the file carries an agent id, it must equal `--agent-id`. |
| `--output FILE` | The reply as a file, or `-` for stdin. The report flags rounds with such replies. |
| `--void` | The agent received the brief but never replied. Needs `--transcript`, which must contain the brief id somewhere (a judge pointed at the brief file counts); a run whose briefs carry no brief id cannot void. Recorded as a void attempt (not valid, source `void`): it clears the block an unrecorded agent puts on the round, but never fills a seat. Every assistant text and handback message is read: when the prompt holds the whole brief and the agent left any reply, the void is refused ("cannot be voided"); when the prompt does not hold the brief, the void is recorded and labelled "voided after replying (brief not verbatim)", and that seat's next reply must come from a transcript holding the pasted brief: `--output` is refused on it. At most 3 voids per slot and round; they do not count toward `max_replacements`. |
| `--sealed` | Record for the sealed panel. |

A reply is rejected (stored, but not counted) when it is not JSON, misses an item or a
dimension, has a score that is not an integer from 1 to 5 (`true` included), a severity outside
the configured list, an empty reasoning, more than a third of quotes not found in the items, a
control mean above `control_max`, or a known-good mean below `good_min`. Scores above the
severity cap are lowered to the cap. A quote that is not found voids that score. A judge that
quoted the same words on every dimension of the candidate is noted for the report.

Refused: a judge slot not in the round's `keys.json`, a round not briefed, an agent id already
used in the run, a reply already recorded, a judge slot that already has a valid reply, a slot
with `max_replacements + 1` attempts, a round already aggregated, a transcript whose prompt
does not contain the whole brief, `--void` without `--transcript` or on a transcript without
the brief id.

## measure

```
sealed-panel measure RUN --round N --id ID --value V [--pass | --fail] [--sealed]
```

Records one instrument reading for a `measured` criterion of the spec. When the criterion has a
`max` or `min`, the value must be a number and the threshold decides; `--pass` and `--fail` are
refused. Without a threshold, one of them is required. Prints `measured ID = V (pass|fail)`.

Refused: an unknown id, a round not briefed yet, a round already aggregated, a criterion
already measured in this round.

## aggregate

```
sealed-panel aggregate RUN --round N [--sealed]
```

Computes, per persona and dimension, the median of the candidate's verified scores, every
judge's score, the severities, the standard deviation, the paired gain against the previous
version, and in a sealed panel the median on the round-1 baseline. Computes the round's noise
(mean standard deviation, floored at `noise_floor_min`), the composite (weighted mean of the
medians, for information only), the best paired gain, the suspect jumps, the findings, the
prompt extras and the judges who reused one quote. Writes `result.json` and appends the round
to `ledger.json`. After a loop round, also writes `RUN/feedback/after_rN.md`, the findings the
author revises from.

Refused: a round already aggregated; a sealed panel with a measured criterion not yet measured;
a judge slot with no real attempt (a void never fills a seat: brief a fresh replacement on the
same brief); an agent
whose transcript (in a folder a recorded transcript came from) holds one of the round's brief
ids but was never recorded; fewer than `min_valid_judges` valid judges for a persona, or
verified scores for a dimension. A transcript folder that no longer exists is skipped.

## status

```
sealed-panel status RUN
```

Read-only. Prints the decision as JSON: `CONTINUE` (with `next_round` and, after a round, the
path of the findings file), `CONVERGED`, `PLATEAU`, `CAP` (each with `"next": "seal"`), or
`SEALED`. Every decision carries `overrides`: the settings that differ from the defaults.

## report

```
sealed-panel report RUN
```

Prints the run's report in markdown: a header with the rubric hash, the decision, the number
of judge contexts and the settings that differ from the defaults; the sealed grade per
dimension with every judge's score and the verdict; the round-1 baseline against the sealed
text, scored by the same sealed panel; the table of rounds with instruments, the informational
composite, best gain, noise and rejected attempts; the open findings; each rejected judge
with its reasons, and each void with its label (no reply, or after replying when the brief was
not verbatim); the repeat-test line when
the sealed text was also judged in the loop; other runs found beside this one; suspect jumps,
injection flags, prompt extras, judges who reused one quote, and rounds with pasted replies.
