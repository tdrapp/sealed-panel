# Protocol

Why each rule exists. The [CLI reference](cli.md) says what each command does; this page says
what each rule protects against.

## 0. Threat model

The adversary is an agent under optimisation pressure that drives the CLI to get a better
grade: it can brief, record, measure, aggregate and seal in any order, as often as the CLI
allows. Every re-roll path through the CLI that this version knows of is refused, or flagged
in the report where it cannot be checked. The adversary is not a person editing the run's
files by hand: a new run directory, a spec edited together with its lock, or a transcript
deleted before aggregation are outside what the code can see, and the report says what it can
(other runs beside this one, pasted replies).

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
  quote, against the halo effect.
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
  status, the duration, the sha256 of the bytes read, the timeout, and the names of the
  environment variables passed. One process per brief, so every runner reply comes from a
  fresh context. A process that fails, times out or writes nothing is a void: no reply is ever
  made up from partial output. A run with a judge command takes runner replies only, on
  every seat: no reply pasted, read from a transcript or voided by hand, so a rejected runner
  reply cannot be swapped for one from another judge. Only the runner records a runner reply:
  the library's `record_reply` refuses the source, so no proof can be written by hand. Through
  MCP, the judge command comes from the configuration the user started the server with, never
  from the caller, and `judge` refuses a run frozen with other judge settings. The judge
  process gets a minimal environment and an empty working directory: no credential beyond the
  ones the user allowed, and no path into the run directory.
- **Transcript (`record --transcript`).** The judge's own transcript must contain the whole
  brief in its prompt; text added around it is counted and reported, and an agent that holds a
  brief id but was never recorded blocks the aggregation.
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
  A judge whose prompt held the whole brief and that left any reply, in any message, cannot be
  voided.
  Pasted replies cannot be checked and are flagged.
- Unmeasured: whether the planted control lifts the candidate by contrast. Runs with and without
  the control would measure it.
- The method is not validated against human graders.
- A verified quote proves the judge saw the text, not that the text supports the score. A judge
  that quotes the same words on every dimension is flagged.
- Replacing rejected judges selects the panel toward judges that pass calibration, which can
  shift it.
- The judges' prompt is checked only through transcripts: text added around the brief is counted
  and reported, but its effect is not measured.
- `record` and `judge` calls for one round must be sequential.
- The runner proves what sealed-panel wrote and read, not what the judge command did in
  between: a command that calls a model several times and prints the best answer is a re-roll
  the runner cannot see. The command is frozen into the run and listed next to the grade, so
  a reader can check what it does.
