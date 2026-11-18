# Protocol

Why each rule exists. The [CLI reference](cli.md) says what each command does; this page says
what each rule protects against.

## 0. Threat model

The adversary is an agent under optimisation pressure that drives the commands and the MCP
tools to get a better grade: it can brief, record, measure, aggregate and seal in any order, as
often as they allow, and it controls every argument of an MCP tool call. Every re-roll path
through them that this version knows of is refused, or flagged in the report where it cannot be
checked. The threat model stops at the run directory: the adversary is not someone with shell
access to it or to the transcript folder. Run files hand-edited or deleted, links planted in a
run, a new run directory, a spec edited together with its lock, or a transcript deleted before
aggregation are outside what the code can see, and the report says what it can (other runs
beside this one, pasted replies). [SECURITY.md](../SECURITY.md) states what is defended, what
is not, and each guard with the threat it answers.

Settings follow the same rule. Every guard has a hard bound, the safety lists (injection
patterns) can be extended but never lose a default entry, and a pattern that matches nothing is
refused. What a configuration may still change is listed in the README's Limits, and every
setting that differs from the defaults is printed next to the grade.

## 1. Independence

- **The author never grades.** Self-assessment skews positive, and a separate skeptical
  evaluator is easier to tune than a self-critical author. The author scopes the evaluation,
  drafts the rubric, writes and revises the text, and reads the findings. The author never
  gives the grade, sees a judge before it scores, shows a judge an earlier score, changes the
  rubric mid-run, re-rolls a panel, or reports a number the sealed panel did not give.
- **A fresh context per brief.** `record` refuses an agent id already used in the run, so a
  judge never scores a later version of something it already scored. Rotating judges after each
  revision keeps the loop from optimising against one judge's taste.
- **Blind.** Labels and item order are shuffled per judge, because position alone can flip a
  comparison. No brief names the candidate, the round, an earlier score, or the author's
  reasoning.
- **Varied stances.** Three reading stances rotate across a persona's judges (first read,
  skeptical, moment of decision), with a random offset per round. Judges from one model family
  correlate; varying the stance is the cheapest decorrelation available on a single-family
  panel. Judges from several families go further.
- **No tools, no paths.** A judge needs only the brief. Giving it tools, or a path into the run
  directory, lets it read the keys and the ledger.

## 2. The rubric

- **Anchors are evidence on the page.** Anchors at 1, 3 and 5 describe what is written ("names
  a number the persona can check"), never adjectives ("convincing"). A rubric is well specified
  when two careful readers would agree on the score.
- **A short discrete scale.** Discrete, anchored options are graded more consistently than a
  wide numeric scale. The scale is 1 to 5, anchors at 1, 3 and 5; 4 is "the 5 anchor except one
  material gap" and 2 is "between 1 and 3".
- **Decomposed.** Three to six dimensions per persona, each its own question with its own
  quote, against the halo effect. This is the method's advice, not a check: a spec sets no
  count, while a template holds three to six dimensions.
- **Instruments take what they can measure.** Length, timing, a format rule or a fact check are
  measured by a script (`measure`), listed in the brief as not the judge's to score, and
  required to pass for CONVERGED.
- **Facts are frozen with the rubric.** Facts are given as text (`facts`) or as a file
  (`facts_file`), never guessed from one value; a file is read by content at `init`, so the hash
  covers it. A claim the facts contradict is an error; a claim they do not cover is unsupported,
  not wrong.
- **Declared choices.** What the commissioner asked for on purpose is listed and not penalised
  as such. Each declared choice carries the commissioner's own words, so the author cannot
  exempt a weakness by declaring it.
- **Frozen and hashed.** The spec and the configuration are hashed at `init`. Any change voids
  every later command; a new rubric is a new run.

## 3. The judge's reply

- **Reasoning, then quote, then score**, per dimension. Reasoning first keeps the score from
  being rationalised after the fact.
- **The quote is checked by code.** Typography, markdown marks, whitespace and case are
  normalised, and an ellipsis may join fragments of the same item in order. A quote that is
  not in the item voids that score; more than a third of void quotes discard the judge.
- **A severity per dimension.** A reviewer asked for flaws always finds one, and a reviewer
  that finds a flaw sometimes approves anyway. So each dimension carries the severity of its
  worst flaw: blocking caps the score at 3, material at 4, and a 5 needs cosmetic or none. The
  question between 4 and 5 becomes a question about a flaw, not a mood. Cosmetic points never
  justify a round.
- **Malformed is rejected, never scored.** A reply that does not fit the shape is stored as
  rejected and replaced by a fresh judge on the same brief, at most twice per slot.

## 4. The control, every round

A deliberately bad version rides in every brief: same topic, length and polish, failing the
1-anchors (generic, unsupported, off-purpose). A judge whose mean on the control is above 2 is
discarded. Checking calibration once, before the loop, says nothing about the judges of a
later round; planting the control every round checks every judge.

## 5. Calibration inside the round

Each judge is calibrated on the round it scores: the control must stay at or below 2 on
average, and a known-good, when the user has one, must reach 4. The known-good is a text the
user validated (a past version they shipped, a reference they named), never one the author
writes. Without one, discrimination is checked downward only.

## 6. Noise and the stop rule for a single text

Statistics over a dataset do not apply to one text. Its only repetitions are its judges, so:

- **Noise** is the mean standard deviation of the candidate's scores across judges, floored at
  0.5.
- **Paired gain.** From round 2, every judge also scores the previous version, blind. The gain
  is the median of each judge's own difference, new minus old. Pairwise comparison by the same
  judge cancels the panel-to-panel noise that makes small absolute deltas unreadable.
- **Stop.** CONVERGED when every median is 5 and every instrument passed. PLATEAU when, two
  rounds running, the best paired gain over all dimensions was below the noise. Paired gains are
  medians of integer differences and move in steps of 0.5, so with the noise between its 0.5
  floor and 1 this means no dimension gained a full point, two rounds running. CAP at
  `max_rounds`.
- **Blocked by a flag.** Every text placed in a brief is scanned: the candidate, the control,
  the previous version, the round-1 baseline and the known-good, and the flags are kept per
  item. A round whose brief carried a flagged item never converges and never takes a sealed
  grade silently: CONVERGED and SEALED become BLOCKED BY FLAG, naming the item, in `status` and
  in the report's title. A blocked loop goes on to the next round with the text removed (the
  removed text still rides along once as the previous version, so that round is blocked too); a
  sealed panel is refused, and a blocked sealed grade is shown but does not stand. Since the
  sealed panel scores the round-1 text as its baseline, a run whose first round was flagged
  cannot take a sealed grade: CONVERGED, PLATEAU and CAP become BLOCKED BY FLAG on `baseline`,
  whose next step is a new run, never the sealed panel. A sealed brief that would carry a
  flagged item is refused before a file is written, so the run's one sealed panel is never spent
  on a grade that could not stand. A text that addresses a judge on purpose (a notice about
  prompt injection, say) needs `injection_acknowledged` in the spec: a reason given by a person
  at the command line, frozen and hashed with the rubric, and shown in the report.
- **Suspect jump.** A paired gain of 2 or more on one dimension is reported for a human read:
  an implausible jump is suspect until checked.

## 7. The author's side

- The author reads findings and reasons, not numbers (`feedback/after_rN.md`), so the next
  revision fixes what the persona would stop on rather than writing toward a score. This is
  hygiene, not a guarantee: the ledger is readable, and the protection is the fresh and sealed
  panels.
- One idea per round, plus a pass that removes what does not serve the purpose.
- Never paste the rubric or the anchors into the text, and never address a judge inside it. The
  injection scan flags it.

## 8. The sealed panel

The grade comes from fresh judges who never fed a revision, with the round-1 version as a
blind baseline. With one text there is no held-out split; the held-out thing is the panel. A
sealed panel can be briefed only after CONVERGED, PLATEAU or CAP, or as a one-shot grade
before any loop round, once per run, and nothing loops after it. Every way to re-roll a grade
(seal early, seal twice, add judges, aggregate again, record one reply twice, resubmit a judged
text, stop recording after the replies one likes, void a judge that replied) that this version
knows of is refused by the CLI, or flagged in the report where it cannot be checked, and each
refusal is covered by a test.
The sealed panel also scores the round-1 text blind, and the report shows baseline against
sealed per dimension. A text changed after the seal is unjudged.

## 9. Proof that a judge received the brief

A grade is only as good as the link between each brief and its reply. Three sources give
three kinds of proof, and the report says which one each round used.

- **Runner (`judge`).** sealed-panel starts the judge process, writes the brief file's bytes to
  its stdin and reads its stdout itself, so nothing stands between the brief and the reply. The
  reply's record stores the proof: the sha256 of the bytes written, the argument list, the exit
  status, the duration, the sha256 of the bytes read, the timeout, the names of the environment
  variables passed, and the sha256 of the value of each name in `[judge] env_allow`, salted with
  a random value `init` writes into the run (`env.salt`); never the value, which may be a
  credential. The argument list is frozen into the run, but an allowed variable can change
  between two attempts, and a template that reads the model from one would then judge with
  another model unseen: `aggregate` compares the hashes of every attempt of a round, rejected
  and void ones included, the report names the variables whose value changed, and it names those
  whose value differs between the sealed panel and the loop rounds. The salt stops a lookup in a
  table made in advance and a match across runs; someone who holds the run directory holds the
  salt and can still guess a short value, so credentials should be long. So the model belongs in
  the argument list (`examples/runner/judge.toml`), and the environment carries credentials
  only. One process per brief, so every runner reply comes from a fresh context, and one process
  per seat at a time: a lock (`fcntl.flock`) held from the seat's check to its record, so
  concurrent or retried calls cannot collect several replies and keep the best. The kernel
  releases the lock when its holder dies, and the attempt a dead runner started is recorded as a
  void by the next call. A process that fails, times out, writes nothing or writes past the cap
  is a void: no reply is ever made up from partial output, and every process started ends
  recorded, at worst as a void. A run with a judge command takes runner replies only, on every
  seat: no reply pasted, read from a transcript or voided by hand, so a rejected runner reply
  cannot be swapped for one from another judge. Only the runner records a runner reply through
  the commands and tools: the library's `record_reply` refuses the source. Someone who writes
  the run's files by hand can still forge one; `SECURITY.md` keeps that out of scope. Through
  MCP, the judge command comes from the configuration the user started the server with, never
  from the caller, and every tool refuses a run frozen with other settings. The judge process
  gets a minimal environment and an empty working directory: no credential beyond the ones the
  user allowed, and no path into the run directory.
- **Transcript (`record --transcript`).** A host adapter reads the transcript, the one the run's
  frozen `[transcript] format` names, from a closed list in code; the protocol reads only the
  neutral record it returns (the prompt, the reply, the tools called, the agent id, the inputs
  after the first output, and whether the session is a fork). An adapter reads its format by its
  own structure and refuses a file it does not recognise, rather than guess. The judge's prompt
  must contain the whole brief; text added around it is counted and reported (an input the host
  marks as its own is not; project instructions are marked so by one format and not by the
  other, see the known limits), and an agent that holds a brief id but was never recorded blocks
  the aggregation. A transcript showing that the judge called any tool other than its format's
  handback tool is rejected, and any item the adapter does not know as a message or reasoning
  counts as a tool call: a judge with a tool could have read the run's keys and known which item
  is the candidate. A session that carries another session's history (a fork) is rejected: that
  history can hold the labels or other judges' replies. An input that reached the judge after
  its first output, beyond what its host adds on its own, is rejected: asking again after an
  answer is a re-roll. A transcript whose last line is cut off is rejected, never read back to
  the reply before it, which may be a draft the judge replaced. The scan for unrecorded agents
  reads the transcripts the adapter lists for the recorded ones (the folder tree or the sessions
  root they sit in), changed since the round was briefed, at most 1000 (more refuse the
  aggregation). A session holding a brief id of the round anywhere and not recorded blocks
  it, as a judge when the id is in its prompt; the one exception is the top-level session the
  recorded judges were launched from, when no brief id is in its own prompt. So judges are
  launched by the top-level session itself. One that holds a brief id and cannot be read, or
  is too large to read whole, refuses it too. Through MCP a
  transcript counts only under a `--transcript-root`, away from the root, its links followed and
  refused unless it stays there: a file the author wrote in its workspace is no proof. Host-mode
  `record` holds the seat's lock too, so two concurrent calls never both pass the seat check. An
  author with shell access to that folder can still forge one, so the runner stays the strong
  proof.
- **Pasted (`record --output`).** Nothing shows that the judge received the brief verbatim,
  or that no other judge was asked and discarded. The report flags such rounds.

## 10. Known limits

- A single-family panel can share a blind spot. Stances reduce it; judges from other families
  cut it further.
- The judges are models, not the persona. The outer calibration is the user's own read: when it
  disagrees with the panel, the rubric is missing a dimension. Add it in a new run.
- The control is usually written by a model too. A control that is bad in an obvious way proves
  only that the judge is awake; a real known-good covers the other side.
- Noise from one panel is optimistic. Two fresh panels on the same text can move a median by a
  point while each panel agrees with itself. The report prints a repeat-test line whenever the
  sealed text was also judged in the loop.
- Selective recording is caught only when replies come from transcripts: a transcript's prompt
  must contain the whole brief, each brief carries a random id, and an unrecorded agent holding
  one blocks the aggregation until it is recorded, or recorded as void when it never replied.
  A void clears that block but never fills a seat: each judge slot still needs a real attempt.
  A judge whose prompt held the whole brief and that left any reply, in any message, or called
  any tool, whichever its name, cannot be voided.
  Pasted replies cannot be checked and are flagged.
- Unmeasured: whether the planted control lifts the candidate by contrast. Runs with and without
  the control would measure it.
- The method is not validated against human graders.
- A verified quote proves the judge saw the text, not that the text supports the score. A judge
  that quotes the same words on every dimension is flagged.
- Replacing rejected judges selects the panel toward judges that pass calibration, which can
  shift it.
- The judges' prompt is checked only through transcripts: text added around the brief is counted
  and reported, but its effect is not measured. What a host adds and marks as its own in the
  transcript (its system messages, attached instruction files) is not counted, before or after
  the first output: project instructions are counted where a format leaves them unmarked, and
  not where it marks them, so the same input is flagged for one host and not the other.
  [integrations.md](integrations.md#transcript-formats) says what each format marks.
- `record` and `judge` calls may overlap: each holds its seat's lock, and the run-wide checks
  (an agent id or a reply used twice in the run, a round briefed twice, a reading lost) run
  under the run's lock. Lock order: a call takes its seat locks first and the run lock last,
  never the reverse (`aggregate`: every seat of the round, then the run), so two calls never
  wait on each other.
- The locks are advisory (`flock`): they bind sealed-panel's own calls, not another program,
  and they may not hold on a network filesystem (NFS). A filesystem that refuses `flock` is
  refused. The run's files are not protected from someone who edits them by hand: deleting a
  started record or a reply, or planting a link, is out of scope (`SECURITY.md`).
- The note on other runs counts the runs that share the run's folder. A run started in
  another folder is a fresh panel it does not see, and every default `grade` run has a folder
  of its own: repeating `grade` on the same text is a re-roll nothing flags. Keep runs side by
  side by giving each `--run` under one folder.
- The runner proves what sealed-panel wrote and read, not what the judge command did in
  between: a command that calls a model several times and prints the best answer is a re-roll
  the runner cannot see. The command is frozen into the run and listed next to the grade, so
  a reader can check what it does.

## 11. The run format

A run freezes its spec and its whole configuration, and `run.json` records the run format
(`schema_version`, 1 since 0.4.0). Every command refuses a run of another format before it
creates anything in it: a run of an earlier release's format is finished with that release,
installed from its tag, a newer one needs an upgrade. Both refusals name the releases that
write each format, from one table (`FORMAT_SINCE` in `sealed_panel/model.py`: each format and
the first release that writes it). A run without `run.json`, or of a format older than the
table, was created before 0.4.0. This version cannot continue a run created before 0.4.0: start
a new run with this version, or finish the old one with the version that created it.

A later release that adds a setting frozen into every run (a key of `defaults.toml`) does one
of two things, so that a run frozen before it keeps its grade:

- it raises the run format and adds the format to `FORMAT_SINCE`: a run frozen without the key
  is then refused as older, naming the releases to finish it with; or
- it keeps the format and supplies, for a run frozen without the key, the value that
  reproduces how that run behaved. It checks the run's `config.lock` against the file as
  written first, so the fingerprint still proves the configuration was not changed.

A release that removes settings frozen into every run applies the second rule the other way:
it keeps the format and reads a run frozen with those settings as the value that behaves the
same, after checking its `config.lock` as written; a run frozen with values no current setting
reproduces is refused, naming them and the release to finish it with. 0.5.0 did this for the
18 transcript field names: a run frozen by 0.4.0 holds them and no `format`, reads as
`agent-jsonl` when they are the 0.4.0 defaults, and is refused, naming them and pointing at
0.4.0, otherwise ([configuration.md](configuration.md#transcript)).

Within one format, a run and the release that opens it can differ in both directions:

- the run was frozen by an earlier release, and its configuration lacks a key this release has
  no value for: the refusal says so, names the key, and names the releases of the format to
  finish the run with, installed from its tag;
- the run was frozen by a newer release, and its configuration holds a key this release does
  not know: the refusal says so, names the key, and says to upgrade.

A configuration that both lacks a key and holds an unknown one is not explained by any one
release, and is refused as damaged. The rule is written next to `SCHEMA_VERSION` in
`sealed_panel/model.py`.

The result file, `RUN/grade.json`, has a format of its own (`schema_version`, 1 since 0.5.0),
separate from the run format. The sealed aggregate step writes it once the sealed round's
result is stored, derived from the run's files by the same function the report prints its
sealed grade from ([api.md](api.md) lists its fields). A reader accepts every result format up
to its own and refuses a later one with the advice to upgrade. A release that adds, removes or
changes the meaning of a key raises the result format, and its reader reads the earlier formats
too, so a comparison across runs reads every file written since 0.5.0. A run sealed before
0.5.0 has no result file: `aggregate --sealed` on it writes one from its round files, with the
run's own `run_schema_version` and, in `sealed_panel`, the release that wrote the file, not the
one that sealed the run.
