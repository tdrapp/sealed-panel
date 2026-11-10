# Security policy

## Supported versions

Only the latest release receives fixes.

## Threat model

sealed-panel defends a grade against its author. The model stops at the run directory: it
covers what an author can do through the documented commands and MCP tools, and nothing an
author can do with shell access to the run directory or the transcript folder.

The adversaries:

- **The author agent.** It is under optimisation pressure and wants a better grade. It drives
  the commands and the MCP tools in any order, as often as they allow, and controls every
  argument of an MCP tool call.
- **A judge.** Its output is untrusted: a reply may be malformed, huge, nested to break a
  parser, or written to claim a grade it did not give.
- **The texts.** A candidate, a control, a known-good, a spec or a transcript may carry an
  injection addressed to the judges or to the tools.

## What is defended

- **The author cannot pick or change the judge through the MCP server.** The judge command,
  its environment names and its timeout come only from the configuration the user started the
  server with, which must lie outside the root; `init` freezes it, and every tool that changes
  a run refuses one frozen with other settings. Through the command line, whoever runs `init`
  chooses the configuration; the report lists the command next to the grade.
- **The author cannot make a tool read or write outside its root.** Every path an MCP tool takes
  (a run, a spec, an artefact, a control, a known-good, a reply file, a library, a text) and a
  spec's `facts_file` are resolved, links followed, and refused unless they stay under the root.
  A transcript is read only under a `--transcript-root`, by the same rule, and a transcript root
  that overlaps the root is turned off. The tools that learn write to the user's rubric library,
  a location the caller does not name.
- **The author cannot get two valid replies on a seat.** Every call that records an attempt on a
  judge seat, the runner's `judge` and host-mode `record` alike, holds that seat's lock
  (`fcntl.flock`) from the seat check to the stored attempt, and `aggregate` holds every seat of
  the round. A second call on a held seat is refused (`judge` skips it). The kernel releases a
  lock when its holder dies; the attempt a dead runner started is recorded as a void by the next
  call, under the void cap. A filesystem without `flock` is refused: nothing is recorded
  unlocked. An MCP `judge` call cancelled by the host keeps its seat until its process ends and
  is recorded, so a retry meets the lock or the recorded reply.
- **In runner mode, no reply is recorded that the runner did not capture.** A run with a judge
  command takes runner replies only, on every seat; the library refuses a runner reply from any
  caller but the runner; every process started ends recorded, at worst as a void (recorded by
  the next call when the runner itself died).
- **Every re-roll path through the commands and tools that this version knows of is refused**,
  or flagged in the report where it cannot be checked (a pasted reply, a changed judge
  environment, text added around a brief).
- **A judge reply cannot break the reader or hang the runner.** A reply past its byte cap, a
  timeout, a non-zero exit, deep nesting or a huge integer ends as a void or a rejected reply,
  never a crash.
- **Run files are written whole and never through a link on the file.** A write goes to a
  private temporary file in the target directory, then replaces the target, so a reader never
  sees half a file and a link planted on a file is replaced, not followed. Every run file is
  written owner-only (0600).

## Out of scope, and why

- **Shell access to the run directory or the transcript folder.** Someone who can write there
  can edit or delete run files, delete a started record, plant a link or a FIFO in a run, edit a
  spec or a configuration together with its lock, start a new run directory, forge a
  transcript, or delete one before aggregating. The code compares what it can (a frozen file
  with its lock, a run's configuration with the server's); it cannot tell who wrote a record,
  and guarding every file against a writer who owns the folder would add code without closing
  the gap. A report is evidence for someone who trusts the machine that made it.
- **The judge command itself.** It executes code by design, with your rights. A command that
  asks a model several times and prints the best answer is a re-roll the runner cannot see, and
  a process that leaves its session survives the timeout. The command is frozen into the run and
  shown next to the grade.
- **An agent with a shell** can run the command line with a configuration of its own; the
  report shows that choice next to the grade.
- **Concurrency across seats.** The lock is per seat: the run-wide checks (an agent id or a
  reply already used in the run) are not serialised across seats, so record one agent at a
  time. `flock` is advisory, binds sealed-panel's own calls only, and may not hold on a network
  filesystem (NFS).
- **Unsupported platforms.** Windows is not supported.

## The judge runner and the MCP server

A `[judge] command` executes code by design, with your rights. Whoever writes the configuration
chooses what runs, so treat that file like a script you would run, and keep credentials out of
it: the command is stored with every reply and shown next to the grade. Only `PATH`, `HOME`,
`LANG` and the names in `env_allow` reach the judge, and they reach it whole.

The MCP server has no authentication: it serves one host over stdio. Anything the root holds can
be read by the caller as an artefact or a spec, so do not make it wider than the texts being
graded.

## Guard map

Each guard 0.4.0 removed, reduced or kept, the threat it answered, and what pins what remains.
"In" and "out" refer to the threat model above. Mutations are entries of
`tools/mutation_check.py`; tests are under `tests/`.

| Guard (0.3.1) | Threat it answered | Scope | Decision in 0.4.0 | Pinned by |
|---|---|---|---|---|
| Write through a directory descriptor, draft opened `O_NOFOLLOW` (`store.write_file`, `_open_dir`) | a link on a run file or on its directory sends the write elsewhere | link on the file: in (cheap to keep); link on the directory: out (planted in a run) | reduced: temporary file in the target directory, then `os.replace`, which replaces a link on the target and never writes through it | `test_a_link_on_the_target_is_replaced_never_followed`, `test_a_link_planted_on_a_round_file_is_not_followed`, `test_a_planted_findings_link_is_replaced_never_followed`; mutations `role text written through a link`, `keys written through a link`, `a failed write leaves its draft` |
| Fallback write without directory descriptors | platforms without `dir_fd` | out (unsupported platforms) | removed | none |
| Draft and file modes (`_modes`) | a draft readable before it replaces a private file | in (confidentiality of run files) | replaced: `mkstemp` creates 0600 and every run file is written 0600 | `test_every_file_is_written_owner_only`; mutation `a run file written readable by others` |
| `read_no_follow`: no link, no FIFO, regular files only (runner brief, markers) | a planted link read as the brief or a marker; a planted FIFO blocks the runner | out (planted run file) | removed; the proof's `brief_sha256` records the bytes sent | none (tests deleted) |
| Seat marker: exclusive `os.link`, pid liveness, age, rename to stale, re-read and link restore, `settle_stale_files` | two judges on one seat; a dead runner's seat stuck or taken over; two settling calls racing | in | replaced: one `flock` per seat held from check to record; a started record turns a dead runner's attempt into a void | `TestSeatLock`, `test_concurrent_calls_judge_a_seat_once`, `test_concurrent_judge_calls_give_one_reply_per_seat`, `test_a_retry_after_a_cancelled_call_never_judges_the_seat_twice`, `test_a_killed_runner_leaves_a_void_for_the_next_call`; mutations `seat marker not exclusive`, `seat lock released before the record`, `runner judge without the seat lock`, `a held seat listed as open`, `running seat aggregated`, `a held seat lock read as unsupported`, `stale files ignored by a judge call`, `stale files ignored by aggregate` |
| (none) host-mode `record` read, checked and wrote with no lock | two concurrent `record` calls both pass the seat check | in | added: the seat lock | `test_two_host_records_on_one_seat_give_one_reply`, `test_host_record_on_a_held_seat_is_refused`; mutation `host record without the seat lock` |
| (none) | a filesystem where `flock` fails, so a seat is recorded unlocked | in | added: refused with a message | `test_a_filesystem_without_flock_is_refused`, `test_a_filesystem_without_flock_starts_no_judge`; mutation `a filesystem without flock records unlocked` |
| (none) | a runner starting a judge on a round aggregated between its first check and its lock | in | added: the round is checked again under the lock | `test_a_round_aggregated_meanwhile_is_not_judged`; mutation `a round aggregated meanwhile judged` |
| Rules for a leftover attempt: void cap, none on a filled seat, a fresh agent id, only command and start time kept, nothing in host mode | a crashed runner's attempt minting voids past the cap, vetoing a seen reply, or carrying an id or content | in (they are the rules for a runner that died) | kept, on the started record | `TestLeftovers`, `TestStartedRecords`; mutations `a stale void on a filled seat`, `a marker's agent id taken`, `a marker's whole content kept`, `a seat at its cap fails every judge call`, `stale files settled in host mode`, `stale marker reused without a void` |
| Leftover marker for a seat the round lacks dropped; linked or FIFO marker dropped | a planted marker | out (planted run file) | removed: started records are looked up per seat, so one for an unknown seat is never read | none (tests deleted) |
| `refuse_links` on every entry of a round directory before a brief | a link planted inside a round before briefing | out (planted run file); the write replaces such a link anyway | removed | `test_a_link_planted_on_a_round_file_is_not_followed` |
| `refuse_links` on a round directory and its parent (`round_dir`) | a link on a round directory sends the whole round elsewhere | out (planted run file) | kept: four lines, shared with the library code, and it also stops a mistaken link | `test_a_link_inside_a_run_is_refused`; mutation `links followed in a run` |
| A frozen configuration key holding a dot | a dotted key shadowing a setting in a flattened view | out (a configuration edited with its lock); also still answered | removed: no default key holds a dot, so the unknown-key refusal refuses it | `test_a_dotted_key_is_refused_as_unknown`, `test_a_shadowing_key_never_reaches_a_tool`; mutation `unknown keys in a frozen configuration accepted` |
| MCP tool paths confined to the root (`path()`) | a tool reading or writing outside the root through `..`, an absolute path, `~` or a link | in | kept, through `store.contained()` | `test_paths_outside_the_root_are_refused`, `TestContainment`; mutations `paths outside the root accepted`, `relative paths resolved against the process directory`, `home paths accepted`, `a path leaving its root accepted` |
| Transcript path: every component checked for a link | a transcript read through a link | a link leaving the transcript root: in; a link inside it: out (shell access to that folder) | reduced: `contained()` under each transcript root, so a path that leaves every root is refused | `test_escapes_are_refused`, `test_a_forged_transcript_under_the_root_is_no_proof`; mutations `links followed in a transcript root`, `a transcript under the root taken as proof` |
| `facts_file` under the root | a spec reading a file outside the root into a run | in | kept, through `contained()` | `test_facts_file_stays_under_the_root`; mutations `facts_file outside the root read`, `init leaves facts_file unconfined` |
| Server configuration outside the root; judge settings checked against the server's | the author rewriting its judge through a tool | in | kept, unchanged | `test_a_configuration_under_the_root_stops_the_server`, `test_a_run_frozen_with_another_judge_is_refused`; mutations `configuration under the root accepted`, `judge settings not checked against the server` |
| Library links: linked inbox or history refused, linked templates and candidates skipped, a frozen library that now resolves elsewhere refused | a link planted in a rubric library | out (shell access to the library) | kept for now: the library code moves later in 0.4.0, and that change decides them | `TestLibraryLinks`, `TestLibraryMoved`; their mutations unchanged |

## Reporting a vulnerability

Report it privately through GitHub security advisories: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue.

Please include the commands or files that reproduce it and what an agent could gain from it.
You should get a first answer within a week.
