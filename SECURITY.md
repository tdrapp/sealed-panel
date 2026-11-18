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
  that overlaps the root is turned off. A run is never created inside another run: the `init`
  tool holds a lock at the root (`.sealed-panel-init.lock`), so two concurrent inits cannot both
  pass that check. The server creates that file at startup and refuses to start when something
  else holds its name, and every tool refuses it and any path under it, so no caller can occupy
  it. The command line has no root, and its user is outside the threat model, so it
  takes no such lock. No tool
  learns: learning is a command a person runs. `rubrics_use` writes a spec skeleton under the
  root.
- **The author cannot get two valid replies on a seat.** Every call that records an attempt on a
  judge seat, the runner's `judge` and host-mode `record` alike, holds that seat's lock
  (`fcntl.flock`) from the seat check to the stored attempt, and `aggregate` holds every seat of
  the round. A second call on a held seat is refused (`judge` skips it, also when the seat was
  filled after it listed the open seats). The kernel releases a
  lock when its holder dies; the attempt a dead runner started is recorded as a void by the next
  call, under the void cap. A filesystem without `flock` is refused: nothing is recorded
  unlocked. An MCP `judge` call cancelled by the host keeps its seat until its process ends and
  is recorded, so a retry meets the lock or the recorded reply. A started record whose attempt
  is already stored, or that is unreadable, never adds a second void or blocks the seat.
- **The run-wide checks hold under concurrent calls.** An agent id judges once per run, a reply
  counts once, a round is briefed once, and every instrument reading is kept: these are read
  and written under the run's lock (`run.lock`), taken briefly by `record`, `judge`'s record
  step, `brief`, `measure` and `aggregate`. A
  call that holds seat locks takes them first and the run lock last, so two calls never wait on
  each other.
- **A setting cannot switch off a guard unseen.** Every setting that guards the protocol has a
  hard bound, and every setting that differs from the defaults is printed next to the grade.
- **A rubric candidate is never promoted or rejected through a tool.** The MCP server has no
  such tool; it returns the command a person runs.
- **In runner mode, no reply is recorded that the runner did not capture.** A run with a judge
  command takes runner replies only, on every seat; the library refuses a runner reply from any
  caller but the runner; every process started ends recorded, at worst as a void (recorded by
  the next call when the runner itself died).
- **In host mode, a transcript is read by its format's adapter, which fails closed.** The run
  freezes `[transcript] format`, and the adapter that format names, from a closed list in code,
  reads every transcript of the run. A file whose structure the adapter does not recognise is
  refused, never guessed. A judge whose transcript shows a call (any item that is neither a
  message nor reasoning, the format's handback tool excepted), a session carrying another
  session's history (a fork, where the format marks one), or an input after its first output is
  rejected; a message from another agent counts as an input, before the first output (in the
  prompt, so the whole-brief check and the extra-text flag see it) and after it (rejected). In
  `agent-jsonl`, an agent's message is one with an `origin` or a queued command (older host
  versions write those with no origin). Before a round is aggregated, the transcripts the
  adapter lists for the recorded ones are searched for the round's brief ids. A session that
  holds one anywhere and is not recorded blocks the round, as a judge when the id is in its
  prompt (its inputs before its first output). The one exception is the top-level session the
  recorded judges were launched from, when no brief id is in its own prompt: it may read the
  briefs and pass them on. That session is the author's own: neither fresh nor blind, so its
  reading a brief is no discarded verdict. A fresh top-level session can only be started from a
  shell, which is out of scope. So judges are launched by the top-level session itself, each as
  a fresh subagent; a helper or standby agent that handles a brief blocks the round. A file that
  cannot be read, or one too large to read whole, blocks it too.
- **Every re-roll path through the commands and tools that this version knows of is refused**,
  or flagged in the report where it cannot be checked (a pasted reply, a changed judge
  environment, text added around a brief, another run in the same folder). What the transcript
  scan cannot see (a brief shown without its id, files outside its reach) is listed under "What
  a transcript does not prove".
- **A judge reply cannot break the reader or hang the runner.** A reply past its byte cap, a
  timeout, a non-zero exit, deep nesting or a huge integer ends as a void or a rejected reply,
  never a crash.
- **Run files are written whole and owner-only.** A record, a brief, a key or a findings file
  goes to a private temporary file in its directory, then replaces the target, so a reader
  never sees half a file and a link on that file is replaced, not followed. The lock files and
  the judge's stderr log are opened in place. Every file sealed-panel creates in a run is
  owner-only (0600), and so is every directory (0700).

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
- **A run in another folder.** Each run is a fresh panel, and the report counts only the runs
  that share its folder: a run started elsewhere, such as every default `grade` run, which gets
  a folder of its own, is a re-roll the tool cannot see. Keep runs side by side by giving each
  `--run` under one folder.
- **What a transcript does not prove.** In host mode the host launches the judge, and
  sealed-panel sees only the file the host wrote: not the judge's tools, its model or its
  directory, only what the file records. What the host marks as its own in a transcript (its
  system messages, attached instruction files) is not counted as an input; a host message that
  steers a judge is not seen. Lines a format's adapter skips (host metadata, session state)
  are not read: instructions a host injects through them are not seen and not counted.
  The first format (`agent-jsonl`) has no fork marker: a carried history is refused only when
  it shows as a call or puts the brief after the first output. The scan for unrecorded judges
  reads only files changed since the round was briefed (minus 60 seconds), under the recorded
  transcripts' folder tree (`agent-jsonl`) or sessions root (`rollout-jsonl`), and refuses past
  1000 files; a judge whose file lies elsewhere, or whose date was set back, is not found. The
  scan recognises a judge only by a brief id written verbatim in its file: an agent shown a
  brief with its id line removed or split is not found. That is a preview of the brief by an
  agent that is never recorded; sealed-panel cannot stop an author from showing a text to
  another model. What it guarantees is about the recorded panel (each judge blind and fresh,
  given the whole brief verbatim, one valid reply per seat), and that a judge recorded, or
  briefed with its id, cannot be discarded.
- **Field names of a transcript.** Until 0.4.1, `[transcript]` held the field names the one
  reader used; they are no longer settings, so a configuration cannot point the reader at other
  fields. A file that sets one is refused; a run frozen with values other than the 0.4.0
  defaults is refused and finished with 0.4.0.
- **Locks against other programs.** `flock` is advisory: it binds sealed-panel's own calls
  only, and may not hold on a network filesystem (NFS).
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

`run.json` (`{"schema_version": 1}`, written last by `init`) is a marker of the run format, not
part of any hash: every command refuses a run without it, of another format, or with it
damaged. Editing it is a hand edit of a run file, out of scope like any other; no command or
tool writes it after `init`.

| Guard (0.3.1) | Threat it answered | Scope | Decision in 0.4.0 | Pinned by |
|---|---|---|---|---|
| Write through a directory descriptor, draft opened `O_NOFOLLOW` (`store.write_file`, `_open_dir`) | a link on a run file or on its directory sends the write elsewhere | link on the file: in (cheap to keep); link on the directory: out (planted in a run) | reduced: temporary file in the target directory, then `os.replace`, which replaces a link on the target and never writes through it | `test_a_link_on_the_target_is_replaced_never_followed`, `test_a_link_planted_on_a_round_file_is_not_followed`, `test_a_planted_findings_link_is_replaced_never_followed`; mutations `role text written through a link`, `keys written through a link`, `a failed write leaves its draft` |
| Fallback write without directory descriptors | platforms without `dir_fd` | out (unsupported platforms) | removed | none |
| Draft and file modes (`_modes`) | a draft readable before it replaces a private file | in (confidentiality of run files) | replaced: `mkstemp` creates 0600 (`env.salt` included), and the lock files and the judge's stderr log are created 0600 | `test_every_file_is_written_owner_only`, `test_every_file_a_run_holds_is_owner_only`, `test_the_judge_log_is_owner_only`; mutations `a run file written readable by others`, `a judge's stderr log readable by others` |
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
| Library links: linked inbox or history refused, linked templates and candidates skipped (a frozen library that resolved elsewhere was refused until 0.4.0, which froze no library) | a link planted in a rubric library | out (shell access to the library) | kept for now: the library code moves later in 0.4.0, and that change decides them | `TestLibraryLinks`, `TestLibraryMoved`; their mutations unchanged, but for the frozen library's (deleted with it) |
| (none) | a run created inside a round folder through MCP `init`, putting a directory where the round's lock or result goes | in | added: `init` refuses a run path with a run among its ancestors; a lock file that cannot be opened is a clear refusal; the result checks read a file, not any path | `test_a_run_inside_a_run_is_refused`, `test_a_run_is_never_created_inside_a_run`, `test_a_lock_path_taken_by_a_directory_is_a_clear_refusal`; mutations `a run created inside a run`, `a lock file that cannot be opened crashes` |
| (none) | a caller-chosen path creating a file or folder inside a run, or one that passes for what a run or the server owns (a folder named `rubric.lock` made by `init` or `rubrics_use`, after which its parent, the root even, passed for a run and refused every later init) | in | added, as one rule in one guard (`store.outside_runs`), for every path a caller names to create: the init run path, the `rubrics use` / `rubrics_use` skeleton target (also a new `.json` file in an existing folder) and every rubric library. (1) A run is recognised only by `rubric.lock` being a file (`store.is_run`), everywhere that test is made. (2) No component the path would create, nor its last one, may be a name a run or the server is recognised by (`store.RESERVED_NAMES`: `rubric.lock`, `run.json`, the server's init lock), nor `.git`, which would make its folder a git working tree where learning never writes (`store.GIT_MARKER`), in any case; the refusal names the component. (3) The path may not be a run or lie in one, so no other run name (the inventory in `tests/test_files.py`, which a full-run test ties to every name a run creates) is ever created by a caller | `test_no_caller_name_passes_for_a_run`, `test_a_reserved_name_is_refused_on_the_command_line`, `test_every_name_a_full_run_creates_is_in_the_inventory`, `test_nothing_is_created_inside_a_run`, `test_a_skeleton_goes_only_into_a_new_json_file_outside_any_run`, `test_a_run_is_never_created_inside_a_run`, `test_a_library_inside_a_run_is_refused`, `test_a_git_folder_is_never_created_on_the_command_line`, `test_a_git_folder_is_never_created_under_the_root`; mutations `a reserved name created by a caller`, `a .git created by a caller`, `a folder named rubric.lock taken for a run`, `a run created inside a run`, `init does not check for a run above`, `library inside a run accepted`, `rubrics_use writes inside a run`, `a spec skeleton not named .json`, `a spec skeleton creates its folder` |
| (none) | one agent id or one reply recorded on two seats at once (the run-wide checks raced) | in | added: the run lock around the run-wide checks and the store, taken after the seat lock | `test_one_agent_on_two_seats_at_once_is_recorded_once`; mutation `run-wide checks without the run lock` |
| "a round is briefed once", checked then written | two concurrent briefings of one round both accepted | in | added: `brief` holds the run lock from its checks to its last write | `test_two_briefings_of_one_round_give_one`; mutation `a round briefed twice at once` |
| (none) | a started record whose attempt is stored, or an unreadable one, adding a void or blocking the seat | in | added: a stored attempt's record is just removed; an unreadable one gives a void with nothing taken from it | `test_a_started_record_of_a_stored_attempt_gives_no_second_void`, `test_an_unreadable_started_record_still_gives_a_void`; mutations `a started record of a stored attempt voided again`, `an unreadable started record blocks the seat` |
| (none) | a seat filled after `judge` listed the open seats stopping the round | in | the under-lock refusal is `SeatTaken`, so the round skips the seat | `test_a_seat_filled_after_the_listing_is_skipped`; mutation `a seat filled meanwhile stops the round` |
| `measured.json` read, extended and rewritten with no lock | two concurrent `measure` calls, one reading lost | in | added: `measure` holds the run lock | `test_two_readings_at_once_are_both_kept`; mutation `concurrent readings lose one` |
| (none) | two concurrent MCP `init` calls, an outer run and one inside it, both passing the nested-run check | in | added: the `init` tool holds a waited lock at the root (a dot-file) around the checks and writes | `test_concurrent_inits_never_nest_a_run` (30 trials), `test_either_order_of_nested_inits_is_refused`; mutation `concurrent inits not serialised` |
| (none) | a tool argument (a feedback library, say) creating a directory at the lock's name before the first init, so every later init fails | in | added: the server creates the lock at startup (refusing to start when its name holds anything but a file), and `path()`, which every tool path goes through, refuses the lock and anything under it | `test_the_lock_file_is_the_servers_from_startup` (every tool with a path argument), `test_a_server_whose_lock_path_is_taken_does_not_start`; mutations `init onto the server's lock file accepted`, `the lock file not reserved at startup` |
| (none) | run directories readable by others under an open umask | in | added: every directory of a run is created 0700 | `test_every_file_a_run_holds_is_owner_only`; mutation `a run directory readable by others` |
| Setting bounds and the overrides printed next to the grade | a setting that switches off a guard unseen | in | kept, unchanged | `test_every_changed_key_is_printed`; mutations `overrides hidden`, `run bounds off`, `judge timeout unbounded` |
| No promote or reject tool on the MCP server | a rubric candidate promoted or rejected by the author | in | kept, unchanged | `test_tools_names_order_and_annotations`, `tests/test_mcp_schema.py` (no mutation: the guard is an absent tool) |

### Added in 0.4.1: the transcript adapters

| Guard | Threat it answers | Scope | Pinned by |
|---|---|---|---|
| A transcript of unknown structure refused (`FormatError`), nothing stored | a reader that skips what it does not know misses a call or a later input | in | `test_an_entry_of_another_shape_is_refused`, `test_a_file_of_another_format_is_refused`, `test_two_agent_ids_are_refused`, `test_an_item_of_another_shape_is_refused`, `test_a_file_without_a_session_record_is_refused`, `test_a_session_record_without_an_id_is_refused`; mutations `an entry of another shape read`, `a file without messages read`, `two agent ids read as one`, `an untyped block accepted`, `an assistant string content accepted`, `an attachment without a type accepted`, `a rollout without a session record read`, `a model item of another shape read`, `a session record without an id read` |
| Any item that is neither a message nor reasoning counts as a call | a tool of a new type unseen, so a judge that read the run passes | in | `test_an_unknown_block_counts_as_a_call`, `test_an_unknown_item_counts_as_a_call`, `test_a_judge_that_ran_a_command_is_rejected`; mutations `an unknown assistant block not counted as a call`, `an unknown rollout item not counted as a call`, `a judge that used a tool is read` |
| A fork rejected (`rollout-jsonl`: a fork key or a second session record) | a judge whose context holds the run's labels or other judges' replies | in; a fork in `agent-jsonl`, which has no marker: out, refused only through the prompt and call rules | `test_a_fork_is_rejected`, `test_either_fork_sign_is_enough`; mutations `a forked judge read`, `a fork marker ignored`, `a second session record ignored` |
| An input after the first output rejected; every turn after it counts | asking a judge again after it answered: a re-roll | in | `test_an_input_after_the_first_output_is_refused`, `test_a_second_turn_is_rejected`, `test_a_turn_without_a_message_still_counts`, `test_a_message_within_the_turn_is_rejected`, `test_any_input_item_after_the_output_counts`, `test_reasoning_before_the_answer_is_not_an_output`; mutations `a judge with a later input is read`, `a later string input not counted`, `a later text block not counted`, `a later attachment not counted`, `a later user entry with a tool result and text not counted`, `an agent's attachment after the output not counted`, `a later turn not counted`, `a later rollout message not counted`, `a later agent message not counted`, `the prompt counted as a later input`, `reasoning taken as the first output` |
| A message from another agent is an input: in the prompt before the first output, a later input after it; in `agent-jsonl`, an entry with an origin or a queued command | a steer sent to the judge right after launch, counted nowhere | in | `TestHostOwnMessages`, `test_an_input_after_the_first_output_is_refused`; mutations `an agent's message read as the host's own`, `an origin on the attachment ignored`, `an origin on the entry ignored`, `a queued command without an origin read as the host's`, `an agent's attachment left out of the prompt`, `an agent attachment without a prompt left out` |
| The host's own messages are not inputs | a legitimate judge refused for what its host repeats | in (a false refusal) | `test_a_host_message_after_the_output_is_not_an_input`, `test_a_tool_free_judge_is_read_with_no_prompt_extra`, `test_a_one_turn_tool_free_judge_is_read`; mutations `the host's own message counted in the prompt`, `the host's own message after the output counted as an input`, `the host's snapshot counted as a later input`, `the host's developer messages counted in the prompt` |
| The scan for unrecorded judges: bounded, refusing past the cap, an unreadable file holding a brief id blocking | a judge launched and kept quiet; a scan that stops short | in | `test_the_scan_is_bounded`, `test_an_unreadable_sibling_holding_a_brief_id_blocks_the_round` (both formats), `test_an_unreadable_sibling_without_a_brief_id_is_skipped`, `test_a_file_older_than_the_briefs_is_not_scanned`, `test_a_rollout_older_than_the_briefs_is_not_scanned`; mutations `the sibling scan unbounded`, `an unreadable sibling holding a brief id read by its file name`, `every sibling parsed, a brief id or not`, `transcripts older than the briefs scanned`, `rollouts older than the briefs scanned` |
| The scan's reach: the whole `subagents/` tree and the main session file (`agent-jsonl`); the whole sessions root, whatever the date folder or parent (`rollout-jsonl`) | a judge launched from a workflow folder, by a helper agent, as the main session, or in a session filed days earlier | in | `test_a_workflow_subfolder_is_scanned`, `test_judges_in_a_workflow_subfolder_scan_the_whole_tree`, `test_the_host_side_files_are_not_scanned`, `test_the_workflow_journal_is_not_scanned`, `test_another_unknown_file_holding_a_brief_id_blocks`, `test_a_main_session_started_with_a_brief_is_a_judge`, `test_a_main_session_named_by_the_entries_session_id_is_read`, `test_a_judge_started_by_a_helper_subagent_blocks_the_round`, `test_a_session_filed_days_earlier_blocks_the_round`, `test_a_sibling_filed_the_next_day_blocks_the_round`; mutations `a nested subagents folder not scanned`, `the subagents tree not found`, `the host side files scanned`, `the workflow journal scanned`, `the main session not scanned`, `the session id of the entries ignored`, `the session folder's name ignored`, `a session filed in another date folder not scanned` |
| Only the top-level session the recorded judges were launched from is exempt, and only when no brief id is in its prompt; any other unrecorded session holding a brief id anywhere blocks, as a judge when it is in its prompt | a judge that briefs a second judge and keeps the better; a standby agent shown the brief later; a helper; a judge run as the main session | in | `test_a_launcher_with_the_brief_in_its_prompt_is_a_judge`, `test_a_launcher_without_the_brief_in_its_prompt_is_exempt`, `test_a_helper_that_launched_the_judges_blocks_the_round` (both formats), `test_a_helper_given_the_brief_text_blocks_the_round`, `test_a_helper_given_the_brief_text_is_a_judge`, `test_a_standby_agent_shown_the_brief_blocks_the_round` (both formats, later input and tool output), `test_another_session_holding_a_brief_elsewhere_blocks_the_round`, `test_a_main_session_that_passed_the_briefs_on_is_exempt`, `test_a_main_session_that_read_the_briefs_through_a_tool_is_exempt`, `test_another_subagent_holding_a_brief_elsewhere_blocks`, `test_the_top_level_session_is_found_above_a_helper`, `test_an_agent_recorded_from_a_pasted_reply_is_recorded`, `test_an_id_that_is_not_a_plain_name_names_no_file`; mutations `the top-level session with a brief in its prompt exempt`, `the top-level session without a brief in its prompt refused`, `a session holding a brief elsewhere passed`, `a recorded session counted as unrecorded`, `sessions holding a brief elsewhere ignored`, `the top-level session not found`, `a helper taken for the top-level session`, `the main session not taken for the top-level session`, `a session id with a path read as a file name` |
| No silent size skip: a file is searched for brief ids whatever its size; one holding a brief id and too large to read whole blocks; the top-level session's prompt is read from the start of its file, and a start without an output blocks | a judge hidden in a file past the cap | in | `test_a_brief_id_split_across_two_chunks_is_found`, `test_a_large_subagent_holding_a_brief_blocks`, `test_a_large_subagent_without_a_brief_is_skipped`, `test_a_large_main_session_is_read_from_its_start`, `test_a_large_main_session_with_a_brief_in_its_prompt_is_a_judge`, `test_a_main_session_with_no_output_within_the_cap_blocks`, `test_a_main_session_whose_start_shows_no_output_blocks`; mutations `a brief id across two chunks missed`, `a large session holding a brief read anyway`, `the top-level session read whole past the cap`, `a top-level session with no output in its start exempt`, `an unreadable start of a top-level session read` |
| The transcript field names removed; a closed list of formats | a configuration pointing the reader at fields that hide a call or an input | in | `test_a_removed_field_name_is_refused_by_name`, `TestFrozenBeforeTheAdapters`, `test_out_of_bound_values_refused`; mutations `a removed transcript field name accepted`, `an unknown transcript format accepted`, `a run frozen with other field names read as the default format`, `the forward rule read before the lock check` |

## Reporting a vulnerability

Report it privately through GitHub security advisories: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue.

Please include the commands or files that reproduce it and what an agent could gain from it.
You should get a first answer within a week.
