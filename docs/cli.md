# Command line

```
sealed-panel [--version] <command> RUN [options]
```

`RUN` is the run directory; `rubrics` works on the rubric library instead (see below). Only
`init` accepts `--config PATH` (see [configuration.md](configuration.md)); every later command
reads the configuration frozen into the run.

A run frozen by a version before 0.4.0 is read only: `status` and `report` read it, and every
command that writes to a run (`brief`, `record`, `judge`, `measure`, `aggregate`, `learning
learn`, `feedback`) refuses it, naming the version that froze it, so finish it with that
version.

`--round N` is required for a loop round. With `--sealed` it is optional and defaults to 1,
since a run has one sealed panel.

Every refusal prints `sealed-panel: <reason>` on stderr and exits with status 2. So does
malformed input (a file that is not JSON, a spec or run file of the wrong shape): the message
names the problem, never a traceback. A rejected judge reply is not a refusal: `record` stores
it, prints `REJECTED`, and exits 0.

`record` and `judge` calls may overlap: each holds its judge slot's lock from the slot's check
to the stored attempt, and a call on a slot another call holds is refused with "seat J is held
by another call" (`judge` skips it). The run-wide checks (an agent id or a reply already used in
the run, a round already briefed, the round's readings) run under the run's lock, which
`record`, `brief`, `measure` and `aggregate` wait for briefly. The locks are `fcntl.flock` on
`seat_j<J>.lock` beside the brief and on `run.lock` in the run; a filesystem that does not
support them is refused, never written unlocked.

## init

```
sealed-panel init RUN --spec SPEC.json [--config CONFIG.toml] [--library DIR]
```

Validates the spec's shape and values, fills the per-run defaults, reads `facts_file` by
content, and refuses a spec whose own text addresses the judges. Writes `spec.json`,
`rubric.lock` (sha256 of the rubric), `config.json`, `config.lock` and `env.salt` (the salt of
the judge environment hashes). Refuses if `RUN` is already initialised (a new rubric is a new
run), if it lies inside another run, or if it holds other files: `init` takes a new or empty
directory, so it never writes over another program's files.

A spec has `title`, `purpose`, `personas` (each with `id` and `description`) and `dimensions`
(each with `id`, `question`, `anchors` at `"1"`, `"3"` and `"5"`, and optionally `name`,
`weight`, the anchors `"2"` and `"4"`, and `personas` to restrict it). Optional: `facts` (text)
or `facts_file` (a path to a file that must exist, read and frozen by content; not both),
`measured` (each with `id`, `description`, and an optional numeric `max` or `min`),
`declared_choices` (each with `choice` and `user_said`, the words of the person who asked for
it), and the keys of the `[run]` configuration table, within their bounds. `template`
(`{id, version, sha256}`, written by `rubrics use`) records the template the spec started from
and is part of the rubric hash; `init` checks it against the library (`--library DIR`, else
`$SEALED_PANEL_RUBRICS`, or the user-level default), refuses an unknown id or version naming
the library it read, recomputes `sha256`, and adds `modified`. No library is frozen into the
run. Unknown keys are refused at every level, and so is any
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
| `--sealed` | Brief the sealed panel. Allowed only after CONVERGED, PLATEAU or CAP, or before any loop round (a one-shot grade). Once per run. Refused before anything is written while the ledger says BLOCKED BY FLAG (a flagged round-1 text would come back as the baseline), or when any text of the sealed brief (candidate, control, baseline, known-good) addresses a judge and the spec has no `injection_acknowledged`: start a new run, or acknowledge it at init with the command line. |

From round 2, the previous round's text rides along as a blind paired comparator. In a sealed
panel, the round-1 text rides along as a blind baseline when it differs from the candidate.

Refused: a round number below 1, a round already briefed, a text already judged in this run, a
round past `max_rounds`, a loop round after the loop stopped or after a seal, a second sealed
panel, a control identical to the candidate, a text containing the item tag, a symbolic link
on the round directory or its parent. A link on a file inside the round is replaced, never
written through.

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
tool call by the judge other than the configured handback tool (a judge gets no tools), or a
transcript whose last non-empty line is not a JSON object (cut off while being written: the
final reply may be lost, and the text before it is not that reply). So record a transcript only
after the judge has returned: recording a file still being written stores a rejection, and the
seat needs a replacement judge. Other unreadable lines are skipped and counted in the result as
`unreadable_lines`. Scores above the severity cap are lowered to the cap. A quote that is not
found voids that score. A judge that quoted the same words on every dimension of the candidate
is noted for the report.

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
reply. The reply is recorded as `record` would,
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

One process per slot at a time: from the slot's check to its record, the runner holds the slot's
lock. A second call skips a held slot, `judge --judge J` on it is refused, and `aggregate`
refuses the round while a slot is held. Before its process starts, the runner writes
`started_j<J>_<id>.json` beside the brief (its agent id, command and start time) and removes it
once the attempt is recorded. A runner that dies in between leaves the record; the kernel
releases its lock with its process, and the next `judge` or `aggregate` records the attempt as a
void, never a free slot. That void counts against the void cap, gets a fresh agent id and keeps
only the command and start time from the record (nothing from an unreadable one); on a slot that
already holds a valid reply, past the cap, or when the attempt it names is already stored, the
record is dropped without one. Started records are read only in a run with a judge command. A
slot another call filled after this call listed the open slots is skipped. Every process started
ends recorded: a reply the record refuses (one already recorded) and an interrupted run are
recorded as voids.

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
prompt extras and the judges who reused one quote. Writes the round's `result.json`, the only
record of the round: `status`, `report` and the next round read the rounds from these files
(`ledger.json`, a copy of them, is no longer written; a run frozen before 0.4.0 keeps its own,
which agrees with its rounds since 0.4.0 never writes to it). After a loop round, also writes
`RUN/feedback/after_rN.md`, the findings the author revises from. Nothing is written outside the
run: before 0.4.0 a sealed panel also wrote learned candidates to the user library's inbox; that
is now `learning learn`.

`aggregate` holds every seat of the round, then the run's lock, for the whole step. In a run
with a judge command, it first records the attempt of a runner that died as a void (see
`judge`).

Refused: a round already aggregated; a seat held by another call ("seat J is held by another
call": a judge running, or a host `record` in progress); a sealed panel with a measured
criterion not yet measured; a judge slot with no real attempt (a void never fills a seat: brief
a fresh replacement on the same brief); an agent whose transcript (in a folder a recorded
transcript came from) holds one of the round's brief ids but was never recorded; fewer than
`min_valid_judges` valid judges for a persona, or verified scores for a dimension. A transcript
folder that no longer exists is skipped.

## status

```
sealed-panel status RUN
```

Read-only. Prints the decision as JSON: `CONTINUE` (with `next_round` and, after a round, the
path of the findings file), `CONVERGED`, `PLATEAU`, `CAP` (each with `"next": "seal"`),
`SEALED`, or `BLOCKED BY FLAG`: an item of the brief of a round that would converge, or of the
sealed panel (the candidate, the control, the previous version, the baseline or the known-good),
contains text addressed to a judge, and the spec has no `injection_acknowledged`. A blocked
decision names what it replaced (`blocked`), the items (`roles`), the patterns that matched
(`flags`) and the next step: the next round with the text removed, or a new run. Every decision
carries `overrides`: the settings that differ from the defaults, and `injection_acknowledged`
when the spec has one.

## report

```
sealed-panel report RUN
```

Prints the run's report in markdown. A blocked run says `(BLOCKED BY FLAG)` in its title, and
a blocked sealed grade is preceded by a line saying it does not stand. Then a header with the
rubric hash, the decision, any `injection_acknowledged` reason, the number
of judge contexts and the settings that differ from the defaults; the sealed grade per
dimension with every judge's score, the verdict and the declared judge model (`[judge]
model_id`, or "not declared"); the round-1 baseline against the sealed
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
candidate in the user library (`--library DIR`, else `$SEALED_PANEL_RUBRICS`, else the
user-level default). With `--dimension`, the candidate proposes to
rewrite that dimension's anchors and carries the sealed judges' quotes on it; without, to add
a dimension. Refused before the run is sealed, for an unknown dimension, with an empty note,
when the same disagreement was rejected before for this template version, or when it was
already promoted into a newer version of the template (the message says which).

## learning learn

```
sealed-panel learning learn RUN [--library DIR]
```

Reads a sealed run and writes its rubric-level candidates (see [rubrics.md](rubrics.md)) to
the user library's inbox, then prints how many and where. It is the only way candidates are
learned: `aggregate` never calls it, and the skill runs it after a sealed panel. The run is only
read. Evidence already recorded is not added again. Refused before the run is sealed. Before
0.4.0 this was `sealed-panel learn RUN`, and every sealed `aggregate` ran it.

## mcp

```
sealed-panel mcp [--root DIR] [--config CONFIG.toml] [--transcript-root DIR ...]
```

Serves the commands as MCP tools over stdio, for an agent host, and exits when stdin closes.
Needs the `mcp` extra; without it, the command says so, with the install line, and exits with
status 2. stdout carries the protocol only; logs go to stderr. Every path a tool takes must
resolve, symbolic links followed, under the root: `--root`, else `[mcp] root` in the
configuration, else the directory the server starts in. A path outside it, or one starting with
`~`, is refused, whether given absolute or relative, and a `facts_file` in a spec too. The
`init` tool holds a lock at the root (`.sealed-panel-init.lock`), so two inits never run at
once. It is the one file the server keeps at the root: the server creates it at startup, and
refuses to start when anything but a file holds its name, or when it cannot create it (a root it
cannot write). Every tool refuses that file and any path under it, under any name that reaches
it: compared by inode, another case of the name on a case-insensitive filesystem, or a hard
link, is refused as the lock.

`--config` is the configuration of every run the server changes, and the only place a
`[judge] command` comes from. It is read once at startup and must live outside the root, or the
server does not start. The `init` tool freezes it and takes no configuration from the caller.
The read-only tools (`status`, `report` and `rubrics_suggest`) read any run whose lock and
shape hold; every tool that changes a run refuses one whose settings differ from the server's
(a setting the run lacks counting as its default), and
`status` says whether the run is `served`. A run from another configuration, or one whose
setting changed default since it was frozen, is changed through the command line or a server
started with that configuration.

`--transcript-root DIR`, repeatable, names a folder where the host writes judge transcripts. A
leading `~`, `$HOME` or `${HOME}` is the user's home. It is read only, for the transcript of
`record` (a void included). Through MCP a transcript is accepted only there, resolved with its
links followed and refused unless it stays under a transcript root: a file the caller can write
is no proof. A transcript root that overlaps the root (a host started from the home folder,
whose transcripts sit under it) is turned off rather than stopping the server: `status` and
every transcript refusal say why. Start the host from a project directory to read transcripts
again.

The ten tools, in this order: `init`, `brief` (`sealed=true` for `brief --sealed`), `record`
(`void=true` for `record --void`), `judge`, `measure`, `aggregate`, `status`, `report`,
`rubrics_suggest`, `rubrics_use`. Each calls the same function as the command, so every refusal
is the same; a refusal, a missing file or a file of the wrong shape comes back as a tool error
with its reason, and any other error is masked. Each returns a structured result (and the same
object as text). `record` takes exactly one of `transcript`, `reply_file` or `reply` (the reply
text, flagged as pasted); a void takes the `transcript`. `judge` reports progress after each
judge and stops after an hour; a judge already started then still finishes and is recorded.
`rubrics_use` writes the spec skeleton under the root, and refuses a path that exists.

Before 0.4.0 the server had fourteen tools: `seal` is now `brief` with `sealed=true`, `void` is
`record` with `void=true`, `rubrics_list` and `rubrics_show` gave way to `rubrics_suggest` and
`rubrics_use`, and `feedback` is a command only. Learning (`feedback`, `rubrics promote`,
`rubrics reject`) has no tool: a host can answer a confirmation by itself, so a tool call
proves no person decided.
See [integrations.md](integrations.md) for host configuration.

## rubrics

```
sealed-panel rubrics list    [--library DIR]
sealed-panel rubrics suggest --kind K [--text FILE] [--top 3] [--library DIR]
sealed-panel rubrics show    ID [--library DIR]
sealed-panel rubrics use     ID --out SPEC.json [--persona-file FILE] [--library DIR]
sealed-panel rubrics promote CANDIDATE_ID [--into TEMPLATE_ID] [--yes] [--library DIR]
sealed-panel rubrics reject  CANDIDATE_ID --reason TEXT [--library DIR]
```

The library is `--library DIR`, else `$SEALED_PANEL_RUBRICS`, else the user-level default
(`--run RUN`, which read the library frozen into a run, is gone with that frozen library).
Unreadable or invalid user templates and inbox files are skipped with a warning on stderr. See
[rubrics.md](rubrics.md) for the formats.

| Command | Does |
|---|---|
| `list` | Every template (built-in and user) with its version and source, flagging a user template whose built-in is newer than its base; then the open candidates with kind, template version, dimension, count, and any earlier rejection with its reason. |
| `suggest` | The templates whose kinds or aliases match the kind (case, spaces and underscores ignored), each with `when_to_use` and its dimensions; keywords found as whole words in `--text` only order them. With no match: "no template for kind K" and every template's `when_to_use`. Refused: `--top` below 1. |
| `show` | Prints the served template (the user one when it overrides a built-in). Refused: unknown id. |
| `use` | Writes a spec skeleton with `template: {id, version, sha256}` (`init` adds `modified`) and TODO markers for title, purpose, facts (with the template's hint), each suggested instrument threshold and, without `--persona-file` (a JSON list of `{id, description}`), the persona descriptions. Refused: an existing `--out`, a persona file that is not a list of personas. |
| `promote` | Prints the current and proposed text side by side and the file it writes, then asks for the candidate id to be typed (with no terminal it refuses unless `--yes`). Applies the change to the file serving the template (copying a built-in first), bumps the version above the user and built-in versions, appends provenance, and moves the candidate to `history/`, all or nothing. `--into` takes precedence over the candidate's template. Refused: an id that is not a candidate id, a candidate not open, TODO markers in the fields the action uses, an unknown action, a change that breaks the template format, an unreadable user file for that id, no template without `--into`. |
| `reject` | Moves an open candidate to `history/` with the reason; the same signal is not reopened for that template version. Refused: an empty reason. |
