# Command line

```
sealed-panel [--version] <command> RUN [options]
```

`RUN` is the run directory; `rubrics` works on the rubric library instead (see below). Only
`init` accepts `--config PATH` (see [configuration.md](configuration.md)); every later command
reads the configuration frozen into the run.

`--round N` is required for a loop round. With `--sealed` it is optional and defaults to 1,
since a run has one sealed panel.

Every refusal prints `sealed-panel: <reason>` on stderr and exits with status 2. So does
malformed input (a file that is not JSON, a spec or run file of the wrong shape): the message
names the problem, never a traceback. A rejected judge reply is not a refusal: `record` stores
it, prints `REJECTED`, and exits 0.

`record` calls for one round must run one after the other, not in parallel: each call reads the
files the previous one wrote. `judge` calls may overlap, since a marker holds each seat.

## init

```
sealed-panel init RUN --spec SPEC.json [--config CONFIG.toml] [--library DIR]
```

Validates the spec's shape and values, fills the per-run defaults, reads `facts_file` by
content, and refuses a spec whose own text addresses the judges. Writes `spec.json`,
`rubric.lock` (sha256 of the rubric), `config.json`, `config.lock` and an empty `ledger.json`.
Refuses if `RUN` is already initialised (a new rubric is a new run), or if it holds other files:
`init` takes a new or empty directory, so it never writes over another program's files.

A spec has `title`, `purpose`, `personas` (each with `id` and `description`) and `dimensions`
(each with `id`, `question`, `anchors` at `"1"`, `"3"` and `"5"`, and optionally `name`,
`weight`, the anchors `"2"` and `"4"`, and `personas` to restrict it). Optional: `facts` (text)
or `facts_file` (a path to a file that must exist, read and frozen by content; not both),
`measured` (each with `id`, `description`, and an optional numeric `max` or `min`),
`declared_choices` (each with `choice` and `user_said`, the words of the person who asked for
it), and the keys of the `[run]` configuration table, within their bounds. `template`
(`{id, version, sha256}`, written by `rubrics use`) records the template the spec started from
and is part of the rubric hash; `init` checks it against the library (`--library DIR`, else
`$SEALED_PANEL_RUBRICS`, the `[library] path` setting, or the user-level default), refuses an
unknown id or version naming the library it read, recomputes `sha256`, and adds `modified`. The
same library is frozen into the run. Unknown keys are refused at every level, and so is any
string that still holds a `TODO(sealed-panel)` marker. Every `measured` field is type-checked
(`id` and `description` strings, `min` and `max` finite numbers with `min` not above `max`,
unique ids), weights must be finite numbers above 0, and `judges_per_persona` is between 2 and
9. A relative `facts_file` is read from the spec file's folder. A `facts` text is refused as a
path, with a pointer to `facts_file`, when it starts with `~/`, `./` or `../`; when it starts
with `/` and holds another slash; when it is one token with a slash; or when it is one line
ending in a file extension. Anything else is text, so "~2/3 of revenue" or "/ marks a break"
stays facts. See `skill/spec-reference.md` and `examples/announcement/spec.json`.

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
panel, a control identical to the candidate, a text containing the item tag, a symbolic link
where the round writes (the round directory, its parent, or an entry inside it).

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
| `--void` | The agent received the brief but never replied. Needs `--transcript`, which must contain the brief id somewhere (a judge pointed at the brief file counts); a run whose briefs carry no brief id cannot void. Recorded as a void attempt (not valid, source `void`): it clears the block an unrecorded agent puts on the round, but never fills a seat. Every assistant text and every tool call, whichever its name, counts as a reply: when the prompt holds the whole brief and the agent left any of those, the void is refused ("cannot be voided"); when the prompt does not hold the brief, the void is recorded and labelled "voided after replying (brief not verbatim)", and that seat's next reply must come from a transcript holding the pasted brief: `--output` is refused on it. At most 3 voids per slot and round; they do not count toward `max_replacements`. |
| `--sealed` | Record for the sealed panel. |

A reply is rejected (stored, but not counted) when it is not JSON, misses an item or a
dimension, has a score that is not an integer from 1 to 5 (`true` included), a severity outside
the configured list, an empty reasoning, more than a third of quotes not found in the items, a
control mean above `control_max`, a known-good mean below `good_min`, or, from a transcript, any
tool call by the judge other than the configured handback tool (a judge gets no tools). Scores
above the severity cap are lowered to the cap. A quote that is not found voids that score. A
judge that quoted the same words on every dimension of the candidate is noted for the report.

Refused: a judge slot not in the round's `keys.json`, a round not briefed, an agent id already
used in the run, a reply already recorded, a judge slot that already has a valid reply, any
reply or void in a run with a judge command (the runner records those), a slot
with `max_replacements + 1` attempts, a round already aggregated, a transcript whose prompt
does not contain the whole brief, `--void` without `--transcript` or on a transcript without
the brief id.

## judge

```
sealed-panel judge RUN --round N [--judge J ...] [--sealed]
```

Runs the judge command frozen into the run (`[judge] command`, set with `init --config`; see
[configuration.md](configuration.md)) once for every judge slot of the round that has no valid
reply, one after the other, or only for the slots given with `--judge`. For each slot it starts
one fresh process from the argument list (never through a shell), in an empty temporary
directory, with an environment holding only `PATH`, `HOME`, `LANG` and the names in `[judge]
env_allow`. It writes the brief file's exact bytes to the process's stdin, closes it, and reads
stdout as the reply; stderr goes to `runner_j<J>_<id>.log` beside the brief, never into the
reply. A brief file that is a symbolic link is refused. The reply is recorded as `record` would,
with source `runner`, under an agent id the runner draws (`runner-<12 hex>`), with its proof
(see [protocol.md](protocol.md)). Prints one line per slot: `VALID judge J`, `REJECTED judge J:
<reasons>`, `VOID judge J: <why>` (then the duration and the log file), or `SKIPPED judge J` for
a slot another call took meanwhile.

A process that exits with a non-zero status, runs past `[judge] timeout_s` (it is stopped, with
every process it started in its session), writes nothing, or writes more than `[judge]
max_reply_bytes` to stdout or `[judge] max_stderr_bytes` to stderr gives no reply: the attempt
is recorded as void, under the cap of 3 voids per slot and round, and never fills a seat. What
it wrote (up to the cap) is stored for a human and never read as a reply. A process that leaves
another process holding its output is stopped at the timeout. Run `judge` again to fill a void
or rejected slot with a fresh process.

One process per slot at a time: from the slot's check to its record, the runner holds the
slot with `running_j<J>.json` beside the brief, created only when absent. A second call skips
a held slot, `judge --judge J` on it is refused, and `aggregate` refuses the round while a slot
is held. A marker whose runner stopped (its process is gone, or it is older than the timeout
plus 60 seconds) is recorded as a void by the next `judge`, never reused silently. That void
counts against the void cap, gets a fresh agent id and keeps only the holder's pid, start time
and command from the marker; a marker on a slot that already holds a valid reply, or for a slot
the round does not have, is dropped without one, and so is a marker that is a link or not a
regular file. Markers exist only in a run with a judge command; elsewhere such a file means
nothing. Every process started ends recorded: a reply the record refuses (one already recorded)
and an interrupted run are recorded as voids.

Refused before any process starts: a run without a judge command (host mode), a round not
briefed or already aggregated, a slot not in the round, every asked slot already holding a
valid reply or held by another call, a slot at its replacement cap or at its void cap. A command
that cannot be started records nothing.
A run with a judge command takes runner replies only: `record`, pasted, from a transcript or
as a void, is refused on every slot. A run without one stays in host mode.

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
author revises from. After a sealed panel, writes learned candidates to the user library's
inbox (unless `[library] learn` is false) and prints how many.

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

## feedback

```
sealed-panel feedback RUN --note TEXT [--dimension ID] [--library DIR]
```

Records the user's own read disagreeing with the sealed grade as a `user_disagreement`
candidate in the library frozen into the run. With `--dimension`, the candidate proposes to
rewrite that dimension's anchors and carries the sealed judges' quotes on it; without, to add
a dimension. Refused before the run is sealed, for an unknown dimension, with an empty note,
when the same disagreement was rejected before for this template version, or when it was
already promoted into a newer version of the template (the message says which).

## learn

```
sealed-panel learn RUN
```

Writes the candidates of a sealed run to its library's inbox, as `aggregate --sealed` does.
Use it when aggregate printed "learning skipped". Evidence already recorded is not added again.
Refused before the run is sealed, or when learning fails again.

## mcp

```
sealed-panel mcp [--root DIR] [--config CONFIG.toml]
```

Serves the commands as MCP tools over stdio, for an agent host, and exits when stdin closes.
Needs the `mcp` extra; without it, the command says so, with the install line, and exits with
status 2. stdout carries the protocol only; logs go to stderr. Every path a tool takes must
resolve, symbolic links followed, under the root: `--root`, else `[mcp] root` in the
configuration, else the directory the server starts in. A path outside it, or one starting with
`~`, is refused, whether given absolute or relative, and a `facts_file` in a spec too.

`--config` is the configuration of every run the server serves, and the only place a `[judge]
command` comes from. It is read once at startup and must live outside the root, or the server
does not start. The `init` tool freezes it and takes no configuration from the caller, and every
tool that changes a run refuses one frozen with other settings (`status`, `report` and the
`rubrics_*` tools still read it, and `status` says whether it is `served`) (the path of its
library aside), so a run from an older version or another configuration needs a server started
for it, or the command line.

`--transcript-root DIR`, repeatable, names a folder where the host writes judge transcripts. It
is read only, for the transcript of `record` and `void`, and may not overlap the root. Through
MCP a transcript is accepted only there, with no symbolic link on the way: a file the caller
can write is no proof.

The tools, in this order: `init`, `brief`, `seal` (`brief --sealed`), `record`, `void`
(`record --void`), `judge`, `measure`, `aggregate`, `status`, `report`, `rubrics_list`,
`rubrics_suggest`, `rubrics_show`, `feedback`. Each calls the same function as the command, so
every refusal is the same; a refusal, a missing file or a file of the wrong shape comes back as
a tool error with its reason, and any other error is masked. Each returns a structured result
(and the same object as text). `record` takes exactly one of `transcript`, `reply_file` or
`reply` (the reply text, flagged as pasted). `judge` reports progress after each judge and
stops after an hour; a judge already started then still finishes and is recorded.

`rubrics promote` and `rubrics reject` are not tools: a host can answer a confirmation by
itself, so a tool call proves no person decided. `rubrics_show`, `rubrics_list` and `feedback`
return the command a person runs instead. `rubrics use` and `learn` stay on the command line.
See [integrations.md](integrations.md) for host configuration.

## rubrics

```
sealed-panel rubrics list    [--library DIR | --run RUN]
sealed-panel rubrics suggest --kind K [--text FILE] [--top 3] [--library DIR | --run RUN]
sealed-panel rubrics show    ID [--library DIR | --run RUN]
sealed-panel rubrics use     ID --out SPEC.json [--persona-file FILE] [--library DIR | --run RUN]
sealed-panel rubrics promote CANDIDATE_ID [--into TEMPLATE_ID] [--yes] [--library DIR | --run RUN]
sealed-panel rubrics reject  CANDIDATE_ID --reason TEXT [--library DIR | --run RUN]
```

The library is `--library DIR`; else, with `--run`, the library frozen into that run; else
`$SEALED_PANEL_RUBRICS`, the `[library] path` setting, or the user-level default. Unreadable
or invalid user templates and inbox files are skipped with a warning on stderr. See
[rubrics.md](rubrics.md) for the formats.

| Command | Does |
|---|---|
| `list` | Every template (built-in and user) with its version and source, flagging a user template whose built-in is newer than its base; then the open candidates with kind, template version, dimension, count, and any earlier rejection with its reason. |
| `suggest` | The templates whose kinds or aliases match the kind (case, spaces and underscores ignored), each with `when_to_use` and its dimensions; keywords found as whole words in `--text` only order them. With no match: "no template for kind K" and every template's `when_to_use`. Refused: `--top` below 1. |
| `show` | Prints the served template (the user one when it overrides a built-in). Refused: unknown id. |
| `use` | Writes a spec skeleton with `template: {id, version, sha256}` (`init` adds `modified`) and TODO markers for title, purpose, facts (with the template's hint), each suggested instrument threshold and, without `--persona-file` (a JSON list of `{id, description}`), the persona descriptions. Refused: an existing `--out`, a persona file that is not a list of personas. |
| `promote` | Prints the current and proposed text side by side and the file it writes, then asks for the candidate id to be typed (with no terminal it refuses unless `--yes`). Applies the change to the file serving the template (copying a built-in first), bumps the version above the user and built-in versions, appends provenance, and moves the candidate to `history/`, all or nothing. `--into` takes precedence over the candidate's template. Refused: an id that is not a candidate id, a candidate not open, TODO markers in the fields the action uses, an unknown action, a change that breaks the template format, an unreadable user file for that id, no template without `--into`. |
| `reject` | Moves an open candidate to `history/` with the reason; the same signal is not reopened for that template version. Refused: an empty reason. |
