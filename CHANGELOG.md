# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Host adapters. A judge's transcript is read by the adapter of its format, from a closed list
  in `sealed_panel.hosts`, and the core reads only the neutral record it returns. Two formats:
  `agent-jsonl` (the default, the format the 0.4.0 reader read) and `rollout-jsonl`, a format
  that opens each session file with a session record. `docs/integrations.md` says which host
  writes which, where its transcripts are and how to launch a judge for it.
- `[transcript] format`, frozen at `init`, names the run's transcript format.
- A judge whose session carries another session's history (a fork) is rejected, and so is a
  judge that got an input after its first output, beyond what its host adds on its own: an
  input after an answer can ask again, a re-roll. Each refusal has a mutation.

### Changed

- **Breaking:** A transcript whose structure its adapter does not recognise is refused (nothing
  is stored) instead of read past: an entry of a checked type with another shape, an untyped
  content block, a file with no message, two agent ids in one file. Before: entries of another
  shape were skipped.
- **Breaking:** Any assistant item that is neither text nor reasoning counts as a tool call,
  whatever its type. Before: only a type ending in `tool_use` did.
- The prompt a transcript is checked against is every input before the judge's first output
  but what the host marks as its own, not only the first user entry: a second one, or a message
  from another agent (an entry carrying an `origin` in `agent-jsonl`, whatever its kind), is
  counted as a prompt extra. A message from another agent after the first output is a later
  input; the host's own message (marked `isMeta`, with no `origin`) is neither.
- The scan for unrecorded judges reads the transcripts the run's adapter lists for the recorded
  ones, last changed since the round was briefed (minus 60 seconds), and refuses the
  aggregation past 1000 rather than stop short. For `agent-jsonl`, every file under the
  session's `subagents/` folder at any depth, the host's `*.meta.json` and `*.prefix.json` side
  files excepted; for `rollout-jsonl`, every session file under the sessions root, whatever its
  date folder or parent, but the sessions that launched the recorded judges. A file that holds a
  brief id of the round and cannot be read refuses it too; a file without one is never parsed.
  Before: every file of the recorded transcripts' own folders was read.
- A run frozen by 0.4.0 holds the transcript field names and no `format`: after its
  `config.lock` is checked as written, it reads as `agent-jsonl` when they hold the 0.4.0
  defaults (docs/protocol.md section 11).

### Removed

- **Breaking:** The 18 field-name settings of `[transcript]` (`role_key`, `handback_tool` and
  the others). Before: a configuration could rename the fields the one reader read, or set
  `handback_tool = ""` to read no handback tool. After: each adapter reads its format's fields
  as constants, and `agent-jsonl` always reads `SubagentHandback`. A configuration file that
  still sets one is refused, naming each; a run frozen by 0.4.0 with values other than the 0.4.0
  defaults is refused, naming them, and is finished with 0.4.0.

## [0.4.0] - 2026-11-16

0.4.0 changes how the package is built more than what it does. Each module has one job, and the
import layers between them are checked in CI. The records a run writes are typed. The threat
model stops at the run directory, and the code and `SECURITY.md` say the same thing. The MCP
server has 10 tools instead of 14, `defaults.toml` has 96 lines instead of 149, and the most
complex function has a complexity of 43 instead of 58. The package is larger than 0.3.1: 7,115
lines of Python in `src/sealed_panel` against 6,108, in 29 files against 18. It grew by 1,007
lines, of which 578 are type declarations and module headers: 406 in `model.py`, the type
declarations, and 172 more in the headers (docstrings and imports) of the other modules; the
rest is spread over the new and split modules. 0.4.0 is the first release published to the
package index. It refuses to read or write runs created before it (start a new run, or finish
the old one with the version that created it), code that imported names now gone from
`sealed_panel.__all__` or from the old modules breaks, and `sealed-panel learn` is now
`sealed-panel learning learn`. Every such change is an entry marked **Breaking** below, first
under Changed and Removed.

### Changed

- **Breaking:** Refusals that the MCP tools also raise no longer name command-line flags: "a
  void needs the agent's transcript", "the round number is required for a loop round", "the
  agent id ...", "say whether it passed". A stop decision's `next` is "brief the sealed panel"
  instead of "seal", which no longer names a tool.
- **Breaking:** writing to a rubric library inside a git working tree. Before: any use of a
  library in a git working tree was refused unless `git check-ignore` said the tree ignored it,
  asked of the user's git. After: no git process runs, so that exception is gone: a library
  with a `.git` at or above it (a directory, or a worktree's file) is refused for every write
  (`learning learn`, `feedback`, `rubrics promote`, `rubrics reject`), ignored or not, with a
  message saying to point `--library` or `$SEALED_PANEL_RUBRICS` at a folder outside any
  repository; every read (`rubrics list|suggest|show|use`, the MCP `rubrics_suggest` and
  `rubrics_use`, `init` from a template) works there, the default library of a user whose home
  is a git tree included.
- **Breaking:** Every run file is written to a private temporary file in its directory and moved
  over the target. Every file sealed-panel creates in a run, the lock files and the judge's
  stderr log included, is owner-only (0600), and every directory of a run is created 0700: a
  file's earlier mode is no longer kept.
- **Breaking:** A dotted key in a frozen configuration is refused as an unknown key.
- **Breaking** for code that imports a module rather than the package. The package is split into
  one module per job, with no change in behaviour: `spec`, `config`, `library` and `learn` are
  gone, learning lives in the `sealed_panel.learning` subpackage, which the core never imports
  (its command moved too: see Removed), `aggregate` computes a round and writes nothing
  (`steps.aggregate_round` takes the locks and stores the result), and the MCP server's
  `build_server` is one function per concern. The import contract keeps each layer above the
  ones it uses and forbids siblings joined by `|` to import each other. A public name that no
  longer imports from its 0.3 module, and where it is now (from an AST scan of both trees):

| Old module | Names | Now in |
|---|---|---|
| `aggregate` | `aggregate_round` | `steps` |
| `config` (gone) | `REMOVED_SECTIONS`, `SEVERITY_NAMES`, `RUN_BOUNDS`, `MIN_VALID_JUDGES_FLOOR`, `MAX_REPLACEMENTS_CEILING`, `MIN_QUOTE_CHARS_FLOOR`, `MIN_FRAGMENT_CHARS_FLOOR`, `MAX_BAD_SHARE_CEILING`, `SEVERITY_CEILINGS`, `MAX_JUDGES_PER_PERSONA`, `OPTIONAL_TRANSCRIPT_KEYS`, `JUDGE_TIMEOUT_BOUNDS`, `REPLY_BYTES_BOUNDS`, `ENV_NAME`, `MODEL_ID_MAX_CHARS`, `merge`, `removed_message`, `run_value_error`, `bound_errors`, `default_data`, `fingerprint`, `Config`, `default_config`, `load_config`, `resolve`, `NOT_GRADING`, `config_differences`, `schema_errors` | `settings` |
| `config` (gone) | `LIBRARY_ENV`, `default_library_dir`, `guard_library`, `library_dir` | `templates` |
| `config` (gone) | `writable_library` | `learning.location` |
| `config` (gone) | `SHOW_LIMIT`, `SHOW_IN_FULL`, `overrides` | `report` |
| `learn` (gone) | `DISAGREE_SPREAD`, `CONTROL_MIN_CANDIDATE`, `CONTROL_GAP`, `proposals_after_seal`, `learn_after_seal`, `user_disagreement` | `learning.learn` |
| `library` (gone) | `TEMPLATE_KEYS`, `OPTIONAL_TEMPLATE_KEYS`, `DIMENSION_KEYS`, `PROVENANCE_KEYS`, `MIN_DIMENSIONS`, `MAX_DIMENSIONS`, `ID_PATTERN`, `Served`, `template_errors`, `builtin_templates`, `user_templates`, `all_templates`, `get_template`, `normalise_kind`, `suggest`, `instantiate`, `write_skeleton` | `templates` |
| `library` (gone) | `CANDIDATE_KINDS`, `ACTIONS`, `CANDIDATE_ID`, `COLUMN`, `inbox_dir`, `history_dir`, `check_candidate_id`, `dedup_key`, `open_candidates`, `add_candidate`, `closed_status`, `apply_change`, `side_by_side`, `plan_promotion`, `apply_promotion`, `promote`, `reject` | `learning.candidates` |
| `record` | `SCORE_MIN`, `SCORE_MAX`, `FENCE_OPENING` | `reply` |
| `record` | `AGENT_ID_SCAN_CHARS`, `handback_messages` | `transcript` |
| `record` | `MAX_VOIDS` | `seat` |
| `record` | `outcome_line` | `report` |
| `record` | `record_measurement` | `measure` |
| `spec` (gone) | `REQUIRED_ANCHORS`, `ANCHOR_LEVELS`, `RUBRIC_KEYS`, `OPTIONAL_HASH_KEYS`, `TODO_MARKER`, `TEMPLATE_REF_KEYS`, `SPEC_KEYS`, `PERSONA_KEYS`, `DIMENSION_KEYS`, `MEASURED_KEYS`, `CHOICE_KEYS`, `dims_for`, `shape_errors`, `dimensions_sha`, `todo_paths`, `validate_spec`, `rubric_hash` | `rubric` |
| `spec` (gone) | `HOME_OR_RELATIVE`, `ENV_SALT`, `SCHEMA_VERSION`, `RUN_FILE`, `NO_MARKER`, `OLDER_RUN`, `SALT_BYTES`, `FACTS_EXTENSIONS`, `looks_like_path`, `run_salt`, `refuse_older_run`, `load_frozen`, `writes_run`, `run_config` | `run` |
| `spec` (gone) | `init_run` | `steps` |
| `store` | `resolve_round` | `steps` |

- **Breaking:** `sealed_panel.__all__` is the stable API, and smaller. Kept: the step functions
  (`init_run`, `write_briefs`, `record_reply`, `record_from`, `record_measurement`,
  `judge_round`, `aggregate_round`, `status`, `render_report`), `Config` and `load_config`,
  `EvalError` and `ConfigError`, `__version__`. Added: `Spec`, a spec written in Python that
  `init_run` takes in place of a spec file and checks the same way; `SCHEMA_VERSION`; and the
  exceptions a caller may catch, `SeatTaken` and `MalformedReply`. No longer imported from
  `sealed_panel` (import them from their module; unstable until 1.0): `build_brief` (`brief`),
  `check_output` (`reply`), `decide` (`decide`), `default_config` (`settings`), `judge_seat` and
  `open_seats` (`runner`), `last_reply` (`transcript`), `load_frozen` (`run`), `normalise` and
  `quote_found` (`text`), `overrides` (`report`), `validate_spec` (`rubric`). Each of them now
  raises `ImportError` from `sealed_panel`, except `decide`: `from sealed_panel import decide`
  still succeeds, but returns the `sealed_panel.decide` module, so old code fails at the call
  ("'module' object is not callable"), not at the import; import the function with `from
  sealed_panel.decide import decide`. New page `docs/api.md`, whose example a test runs. The
  package already shipped `py.typed`.
- **Breaking:** a rubric template whose dimension id is not a string is invalid. Before, the
  template check accepted any dimension id, and put the ids in a set: a list or an object as an
  id raised `TypeError`, which the listing does not catch, so `rubrics list`, `rubrics
  suggest`, the MCP `rubrics_suggest` and `init` with any template stopped with "unusable
  input" for every template of the library. Now such a template is refused with "dimension
  ['a']: id must be a string", skipped from the listing with a warning, and the listing goes
  on. This also refuses an id that is a number, `null` or a boolean, which was accepted: such
  a user template is no longer listed or usable (`rubrics use` refused it before writing
  anything, already), and a spec that cites it by `"template": {"id": ..., "version": ...}`,
  which froze before, is now refused ("spec.template names no known template"). Give the
  template's dimensions string ids.
- The plugin's marketplace entry installs the plugin from the release tag (`"source":
  {"source": "github", "repo": ..., "ref": "v<version>"}`) instead of the repository's default
  branch, the tag `.mcp.json` already pins the server to: between releases, `main`'s skill
  could call tools the pinned server lacks. The release commit bumps both.
- `judge_round` takes `on_seat(seat, result, why_skipped)`, called after each seat; the command
  line's `judge` is `judge_round` with a printer, instead of its own copy of the loop. Output
  unchanged.
- New MCP tool `rubrics_use(id, out, persona_file?, library?)`: writes the spec skeleton of a
  template under the root (refused outside it, or when `out` exists), as `rubrics use` does.
- `record` and `judge` calls on a round may overlap; a call on a held seat is refused (`judge`
  skips it). The refusal now reads "seat J is held by another call (a judge running or a reply
  being recorded)", in place of "judge J is being judged by another call", since a host
  `record` holds seats too.
- A path refused for leaving the root is named as the caller gave it (`facts_file '...'`, or
  the tool argument).
- A transcript under a transcript root may go through a link that stays under that root; one
  that leaves every root is refused.
- `tools/mutation_check.py --anchors-only` checks that every mutation's anchor is found
  exactly once and runs no test; the commit hook runs it when `src/` or the tool changes.
- The records a run writes are typed: `sealed_panel.model` declares each one as a `TypedDict`
  (the frozen spec, a round's keys and seats, an attempt and its item scores, the runner's
  proof, a round's result, the ledger view, the decision), and `settings.JudgeSettings` types
  the `[judge]` section. mypy checks every read and write of them; no conversion code runs and
  the files on disk are the same JSON, byte for byte. The step functions return these types
  (`record_reply` an `Attempt`, `aggregate_round` a `RoundResult`, `status` a `Decision`).
  `RUNNER` moved from `record` to `model`, and the template reference check from `steps` to
  `templates.check_template_ref`.
- The run format has a forward rule, written next to `SCHEMA_VERSION` (now in
  `sealed_panel.model`) and in `docs/protocol.md` section 11: a later release that adds a
  setting frozen into every run either raises the format or supplies the value that reproduces
  how a run frozen without it behaved. The older-run and newer-run refusals name the releases
  of each format from one table, `model.FORMAT_SINCE` (format to first release). Within a
  format, a frozen configuration that lacks a key was "damaged" and now reads "this run was
  frozen by an earlier release of run format 1, without settings this release has no value for
  (missing configuration key ...)", with the releases to finish it with (sealed-panel 0.4.0 or
  later, installed from its tag); one that holds a key this release does not know was "damaged"
  and now reads "this run was frozen by a newer release of run format 1 ... Upgrade
  sealed-panel". A configuration with both is still refused as damaged.
- A spec and a template check their personas and dimensions against one field table
  (`rubric.PERSONA_FIELDS`, `rubric.DIMENSION_FIELDS`, and `rubric.missing_anchors` for the
  required anchors), each keeping its own rules explicit: a template holds exactly the keys
  the table marks for one and 3 to 6 dimensions, a spec may add `weight` and `personas` and
  has no count bound. What each accepts and every error it gives are unchanged, checked on
  20,000 generated specs and templates against the previous validators.
- The docs say what "three to six dimensions per persona" is: the method's advice, which no
  check applies to a spec; a template holds three to six dimensions (`docs/protocol.md`, the
  skill and `spec-reference.md`).

### Removed

- **Breaking:** the command `sealed-panel learn RUN`: it is `sealed-panel learning learn RUN
  [--library DIR]`, and `sealed-panel learn` no longer exists. The `--run` flag of `rubrics
  list`, `suggest`, `show`, `use`, `promote` and `reject` is gone: the library comes from
  `--library` or `$SEALED_PANEL_RUBRICS`. No other command or flag was renamed or removed (every
  command's arguments compared between 0.3.1 and 0.4.0).
- **Breaking:** the `Config` properties of the removed settings: `strip_chars`, `punctuation`,
  `injection_patterns` and `library` (`Config` is in `__all__`). Before: they read `[text]`,
  `[injection]` and `[library]`. After: use `text.STRIP_CHARS`, `text.PUNCTUATION`,
  `text.INJECTION_PATTERNS`, and `templates.library_dir()` for the library; the thresholds are
  `learning.learn.CONTROL_MIN_CANDIDATE` and `learning.learn.CONTROL_GAP`. `normalise` and
  `injection_flags` lost their `cfg` argument, and `fixed_normalise`, `normalise_with` and
  `library_dir`'s `cfg` argument are gone.
- **Breaking:** runs created before 0.4.0. Before: every version read and wrote the runs of the
  versions before it. After: `init` writes `run.json` (`{"schema_version": 1}`, not hashed),
  and every command and MCP tool, read or write, refuses a run without it, before creating
  anything in it, since 0.4.0 changed what a run freezes (or its init was interrupted:
  `run.json` is written last, so start again). The refusal says: "This version cannot continue
  a run created before 0.4.0: start a new run with this version, or finish the old one with the
  version that created it." A `run.json` of a newer format says to upgrade; one that cannot be
  read says it is damaged. No version before 0.4.0 was published to the package index; 0.3.0
  and 0.3.1 were never tagged.
- **Breaking:** `ledger.json`. Before: `init` wrote an empty one and every `aggregate` appended
  its round, a second copy of each `result.json`. After: neither writes it; `ledger.view(run)`
  derives the rounds from the round files (loop rounds first, by number) and the rubric hash
  from `rubric.lock`.
- **Breaking:** Learning on every seal, and `[library]` (`path`, `learn`,
  `control_min_candidate`, `control_gap`). Before: every sealed `aggregate` wrote candidates to
  the inbox of a library frozen into the run at `init` (`library.path`), unless `library.learn`
  was false, and printed `learning` and `inbox`; `sealed-panel learn RUN` retried. After:
  `aggregate` writes nothing outside the run and no longer prints or returns `learning`/`inbox`
  (CLI, MCP `aggregate`, `aggregate_round`); learning is `sealed-panel learning learn RUN
  [--library DIR]`, which the skill runs after a sealed panel; `init` freezes no library; the
  library is `--library`, else `$SEALED_PANEL_RUBRICS`, else the user default; the thresholds
  are constants (4.0, 1.0); and `--run` on `rubrics` commands and `run` on the MCP rubrics tools
  are gone. A file that sets a `[library]` key is refused, naming it.
- **Breaking:** `[injection]` (`patterns`, `samples`). Before: a configuration could add
  patterns (every default had to stay, each matching its sample line). After: the five 0.3.1
  patterns are the constant `text.INJECTION_PATTERNS`, and `injection_flags(text)` takes no
  configuration. A file that sets an `[injection]` key is refused, naming it.
- **Breaking:** `[text]` (`strip_chars`, `punctuation`). Before: a configuration could map
  punctuation and hold the stripped characters for quote matching, within bounds, while every
  other check used the defaults. After: both are constants in `text.py`, equal to the 0.3.1
  defaults, and one `normalise(text)` serves every comparison (`normalise` lost its `cfg`
  argument; `fixed_normalise` and `normalise_with` are gone). A file that sets a `[text]` key is
  refused with a message naming it ("text.strip_chars: no longer a setting, ... fixed in code
  since 0.4.0").
- **Breaking:** MCP tools, 14 to 10 (`tests/mcp_tools.json` regenerated). Before: `seal`,
  `void`, `rubrics_list`, `rubrics_show` and `feedback` were tools. After: `seal(...)` is
  `brief(..., sealed=true)` (its `round` defaults to 1, as `--sealed` does, and a loop round
  needs it); `void(...)` is `record(..., transcript=..., void=true)`; `rubrics_list` and
  `rubrics_show` are replaced by `rubrics_suggest` (kept) and the new `rubrics_use`; `feedback`
  is a command only (`sealed-panel feedback`). Learning has no tool: a host can answer a
  confirmation itself, so only a person at the command line touches candidates.
- The fallbacks for runs of earlier versions, dead now that 0.4.0 refuses those runs: a run
  without `[judge]` read as host mode, the default output caps and an undeclared model for a
  `[judge]` without them, a round's candidate-only injection flags, a key a frozen configuration
  lacks counted as its default, and a void refused for briefs without a brief id (every 0.4.0
  brief has one). A frozen configuration missing a key is refused ("missing configuration key
  ..."), since every 0.4.0 run froze them all.
- The Windows branches (`%APPDATA%` as the library's default, and the `os.name` tests around
  the judge's process group): the package declares POSIX only and was never tested there. No
  change on macOS or Linux.
- The in-progress marker (`running_j<J>.json`), its pid and age liveness checks, the
  rename-to-stale step and its re-read, replaced by the seat lock.
- The FIFO and link refusals on reads of run files (`read_no_follow`), the descriptor-based
  writes and their fallback, the link check on every entry of a round directory, and the
  dotted-key check: each answered a writer inside the run directory, now out of scope.

### Security

- No path a caller names to create (the `init` run, a `--library`, through the command line or
  the MCP tools) may hold `.git`, in any case. An MCP `init` of `.git` made the server's root a
  git working tree, after which every rubric library under it was refused for a write. The
  refusal reads "'.git' is reserved: it would make its folder a git working tree".
- `rubrics use` and the MCP `rubrics_use` write a spec skeleton only to a new `.json` file in an
  existing folder outside any run: a skeleton named `rubric.lock` (or written under a folder of
  that name) would pass its folder off as a run, and the command line could write one inside a
  run. The rule lives where the file is written, so both interfaces share it.
- Nothing a caller names can create something inside a run, or something that passes for what
  a run or the server owns. One guard (`store.outside_runs`) covers every path a caller asks to
  create (the `init` run path, the `rubrics use` / `rubrics_use` skeleton, any rubric library):
  it refuses a path that is a run or lies in one, and a path whose new or last component is a
  name a run or the server is recognised by (`rubric.lock`, `run.json`, the server's init lock),
  in any case, naming the component ("'rubric.lock' is a name reserved for a run's own files").
  A run is recognised only by `rubric.lock` being a file. Before, a skeleton written to
  `run/rounds/r1/result.json` faked an aggregated round, and an `init` or skeleton named
  `rubric.lock` made its parent folder, the root even, pass for a run.
- The MCP tools refuse the root's lock file under any name that reaches it: the check compares
  inodes, so another case of the name on a case-insensitive filesystem, or a hard link, gets the
  tool's own refusal instead of a later, less clear one. `docs/cli.md` now says the server
  keeps that one dot-file at the root and does not start on a root it cannot write.
- One judge per seat now rests on a lock: every call that records an attempt (`judge`,
  host-mode `record`, and `aggregate` for every seat of its round) holds `fcntl.flock` on
  `seat_j<J>.lock` from the seat check to the stored attempt. Host-mode `record` had no guard:
  two concurrent calls on one seat could both pass the check. The kernel releases a lock when
  its holder dies, and a runner's started record (`started_j<J>_<id>.json`) left by a runner
  that died becomes a void for the next call, under the void cap (none when its attempt is
  already stored; an unreadable record still gives one). A filesystem without `flock` is
  refused; `flock` may not hold on NFS.
- The run-wide checks hold under concurrent calls: an agent id used once per run, a reply
  counted once, and a round briefed once are read and written under the run's lock
  (`run.lock`), taken after any seat lock. Two concurrent `record` calls with one agent id on
  two seats, or two concurrent briefings of one round, could both pass before. `measure`
  takes the same lock, so two concurrent readings of one round are both kept.
- `init` refuses a run path inside another run: through MCP the author could put a directory
  where a round's lock or result goes and block the round. The MCP `init` tool holds a lock at
  the root (`.sealed-panel-init.lock`), so two concurrent inits cannot both pass that check.
  The server creates that file at startup, refuses to start when anything but a file holds its
  name, and every tool refuses it as a path argument, so no caller can occupy it.
- One containment guard, `contained()` (resolve, then require the path to stay under the
  root), confines every MCP tool path, a spec's `facts_file`, and a transcript to its
  transcript roots.
- `SECURITY.md` rewritten: the threat model stops at the run directory, states what is defended
  and what is not, and maps each guard to the threat it answers. Decision record
  `docs/decisions/2026-11-10-threat-model-stops-at-the-run-directory.md`.

## [0.3.1] - 2026-11-09

Not tagged and not published: a development milestone, held by commit `5ec3e76`.

### Fixed

- A transcript whose last line was cut off while being written no longer yields the assistant
  text before it as the reply: `record` rejects it as malformed, and counts the other unreadable
  lines it skipped in the result (`unreadable_lines`). Transcript lines end at a newline only,
  so a line separator inside a JSON string no longer splits an entry.

### Added

- `[judge] model_id`: the model the judges run on, as free text (one printable line, at most
  200 characters), frozen into the run and printed next to the sealed grade, or "not declared".
  The docs say to pin a dated model id, never an alias. A run frozen earlier reads as not
  declared.
- Tests for the HTTP path of `examples/runner/judge_template.py`: a 429, a 500, a body that is
  not JSON, or an answer without a text field each end as a void after one call, never a reply.
- CI jobs `build` (the sdist and the wheel build; the wheel installs in a fresh environment with
  the lock's versions, `sealed-panel --version` names the package version, and the MCP server
  answers `initialize`, through `tools/smoke_mcp.py`), `actionlint` (with shellcheck) and
  `pip-audit` (every locked package, by hash). `build` and `actionlint` join the required
  checks in `.github/branch-protection.json`; `pip-audit` stays advisory.
- A test ties `.github/branch-protection.json` to the CI workflow: every required check is a
  job name CI produces, and only the mutation shards and `pip-audit` are left out, on purpose.
- The release workflow builds with the same uv and the same `uv build` command as the CI build
  job, after `uv lock --check`, so the published files are built the way the tested ones were.
- gitleaks scans what is staged (a pre-commit hook) and the whole history (a required CI job,
  from a pinned release checked by its sha256). `.gitleaks.toml` allowlists only the sha256
  digests of the test fixtures and the lock.
- An import contract (import-linter, a required CI job `imports`): `cli` and `mcp_server` sit
  above the core, `store` and `errors` at the bottom, and every module of the package must be
  placed in a layer.
- A complexity ceiling (a required CI job `complexity`): xenon caps the ranks at their current
  level, and `tools/complexity_check.py` freezes every function above rank B at its current
  complexity (`tools/complexity_baseline.json`); a new function must stay at rank B. On a pull
  request, `--against` the base branch fails when an entry present on both sides rose, or when
  the number of entries or their sum rose: the baseline is a budget, so a rename fits and new
  complexity is paid for by removing some elsewhere.
- Docstrings: every public module, class and function of the package has one (ruff `D1`), the
  public API's carry an `Args:` section naming each argument (ruff `D417` and
  `tests/test_docstrings.py`), and interrogate holds the public coverage at 80% or more (a
  required CI job `docstrings`; it is 100% today).
- A commit-msg hook, `tools/check_commit_msg.py`: a Conventional Commits subject under 72
  characters, then a blank line. `pre-commit install` installs it with the other hooks.
- The pull request template asks for the trade-offs and for the verdict of an independent cold
  review, which every pull request carries; `main` still requires no approving review on GitHub,
  with one maintainer (CONTRIBUTING).
- Test tiers: a `slow` marker on every test that starts a process other than git, declared in
  `pyproject.toml` with `--strict-markers`; `pytest -m "not slow"` is the fast tier. A fixture
  fails an unmarked test that starts such a process.
- The judge runner's proof holds, per attempt, the sha256 of the value of each `[judge]
  env_allow` name it passed (never the value), salted with a random salt `init` writes into the
  run (`env.salt`), and `aggregate` and the report flag a round whose attempts ran under
  different values (`runner_env_changed`) and a sealed panel that ran under other values than
  the loop rounds: a model read from an allowed variable could change between a rejected
  attempt and its replacement, or before the seal, unseen. A run frozen without a salt stores
  no hash and compares nothing.
- A snapshot of the MCP tools (names, annotations, input and output schemas) in
  `tests/mcp_tools.json`, and a test that fails on any drift, in both protocol modes.
- Repository docs: a version line in the README linking this file (a test keeps it on the
  package version); `AGENTS.md`, the rules for a coding agent with what enforces each, and the
  neutrality rule; `docs/runbook.md` (run, release, moved from CONTRIBUTING, and recovery:
  yanking a release, an interrupted run, stale markers); dated decision records in
  `docs/decisions/` (zero runtime dependency, briefs built in code, MCP over stdio only, the
  judge command only from the server's configuration); and `docs/plans/`, where a large change
  starts.

### Changed

- Every text placed in a brief is scanned for text addressed to a judge (the candidate, the
  control, the previous version, the baseline and the known-good), with the flags kept per item,
  and a flag on any of them no longer lets the run reach CONVERGED or a sealed grade silently:
  the decision becomes `BLOCKED BY FLAG`, naming the item, in `status` and in the report's
  title, and a blocked sealed grade is shown with a line saying it does not stand. A blocked
  loop goes on with the text removed; a sealed panel is refused. When the round-1 text was
  flagged, the sealed panel would score it as its baseline, so CONVERGED, PLATEAU and CAP become
  `BLOCKED BY FLAG` on `baseline` with a new run as the next step, never "seal"; and `brief
  --sealed` (and the `seal` tool) refuses, before writing anything, a sealed brief that would
  carry a flagged item. A text that addresses a judge on purpose needs the new spec key
  `injection_acknowledged`: a reason, frozen and hashed with the rubric (a spec without it keeps
  its hash), and shown in the report's header, on its grade line and in `status`. Only a person
  acknowledges: the MCP `init` tool refuses a spec that carries the key, and the command line
  freezes it. `feedback` is refused on a blocked grade, which does not stand.
- The development tools moved from the `dev` extra to `[dependency-groups]` (`test`, `dev`,
  `audit`), and `uv.lock` is committed: contributors and every CI job, the mutation shards and
  the nightly run included, install with `uv sync --locked`. The `dev` extra is gone; the `mcp`
  extra is unchanged. Dependabot follows the lock.
- ruff also runs its security rules (`S`, from bandit) and `T20` (no stray `print`). The
  exceptions are per file (the command line, tools, examples, the stand-in judges, tests'
  asserts and subprocesses) or carry their reason inline.
- `examples/runner/judge_template.py` takes the model on its own command line (`-- MODEL
  ARGV...` or `--url URL`), and `examples/runner/judge.toml` puts it in `judge.command`, where
  the run freezes it and the report shows it next to the grade, with `model_id` beside it; the
  environment carries the credential only. The template still reads `JUDGE_MODEL_COMMAND` or
  `JUDGE_MODEL_URL` when it gets no argument.
- The mutation check runs each mutation against the tests that execute its lines, picked from
  a coverage map built in each run, and confirms every survivor on the whole suite. CI runs it
  on every pull request in four shards and merges their reports; a nightly workflow runs the
  whole suite on every mutation. A pytest exit code other than 0, 1 or 2 is now an error, not a
  kill. In CI the check went from 55 to 81 min to about 6 min.

## [0.3.0] - 2026-11-07

Not tagged and not published: a development milestone, held by commit `92199c7`.

### Added

- A judge runner: `sealed-panel judge RUN --round N` starts one fresh process per open judge
  slot from the `[judge] command` argument list (never a shell), writes the brief file's bytes
  to its stdin and records its stdout as the reply, with source `runner` and its proof: the
  sha256 of the brief bytes and of the reply, the argument list, the exit status, the duration
  and the names of the variables passed. stderr goes to a log file beside the brief. The process
  starts in an empty directory with `PATH`, `HOME`, `LANG` and the names in `[judge] env_allow`
  only. A non-zero exit, a timeout (`[judge] timeout_s`, 1 to 3600 s), no output, or output past
  `[judge] max_reply_bytes` or `max_stderr_bytes` (1 MiB by default, 1 KiB to 16 MiB) is
  recorded as a void, under the void cap; every seat refusal is checked before a process starts.
  A marker holds each seat from its check to its record, so concurrent or retried calls never
  judge it twice; a marker a stopped runner left becomes a void; every process started ends
  recorded. A run with a judge command takes runner replies only, and only the runner can record
  one. The library exposes `judge_round`, `judge_seat`, `open_seats` and `record_from`.
- An MCP server, `sealed-panel mcp`, in the optional `mcp` extra: stdio only, tools `init`,
  `brief`, `seal`, `record`, `void`, `judge`, `measure`, `aggregate`, `status`, `report`,
  `rubrics_list`, `rubrics_suggest`, `rubrics_show` and `feedback`, each calling the same
  function as the command. Structured results, strict input validation, refusals as tool errors
  and other errors masked. The settings come only from the file given to `sealed-panel mcp
  --config`, read once and kept outside the root, never from a tool call, and every tool that
  changes a run refuses one frozen with others (the read-only tools read it, and `status` says
  whether it is served). Every path, a spec's `facts_file` included, must resolve under the root
  (`--root`, `[mcp] root`, or the starting directory). A transcript counts only under a
  `--transcript-root`, away from the root. Promoting and rejecting candidates stay with a
  person.
- The repository is a plugin and its own marketplace for agent hosts that install plugins
  from git: an MCP server started with `uvx`, the skill under `skills/sealed-panel/`, and an
  `eval-judge` agent with an empty tool list that also denies every built-in tool. It asks for
  the host's transcript folder when it is enabled. The skill is also under
  `.agents/skills/sealed-panel/`. `skill/` stays the one source, and a test fails when a copy
  drifts.
- `docs/integrations.md`: MCP hosts, the plugin, TOML host configuration, API pipelines.
  `examples/runner/`: a provider-neutral judge command template, its configuration and a
  library example.
- A release workflow: a tag `v*` builds the sdist and the wheel and publishes them through
  trusted publishing. Every workflow pins its actions by commit. The sdist holds everything its
  tests read.
- CI runs the tests on Python 3.11 to 3.13 without the `mcp` extra as well, where the MCP
  tests skip.

### Changed

- A frozen configuration with an unknown or dotted key is refused, and runs are compared with a
  server section by section, a key an older run lacks counting as its default. Markers left by a
  stopped runner count only in a run with a judge command, under the void cap and a fresh agent
  id; a planted one on a filled or unknown seat, or one that is a link or not a regular file, is
  dropped. A library whose folder became a link is refused. A tool call of another block type
  named like the handback tool is not exempt. A draft replacing a file starts private.
- `transcript.handback_tool` defaults to `SubagentHandback`, the tool a host imposes in its auto
  mode, where the final text is no longer delivered. A run frozen earlier keeps its value.
- `record` rejects a transcript reply whose judge called any tool other than the configured
  handback tool, of any block type ending in `tool_use`: a judge with a tool could have read the
  run's keys. `--void` counts any tool call as a reply, so a judge that replied through a tool
  cannot be voided. A judge with tool calls and no reply is recorded as rejected.
- A reply is read in linear time, and one built to break the parser (deep nesting, a huge
  integer) is rejected.
- `init` refuses a non-empty directory that is not a run. Every file of a run goes through one
  helper: written whole under a temporary name, moved into place, never through a link, keeping
  the mode of the file it replaces. A symbolic link on a run's folders, on a library's `inbox`
  or `history`, or on a brief, template or candidate that is read, stops the command or is
  skipped with a warning. A rubric library inside a run is refused.
- The report labels a void by the runner with its reason, and notes rounds whose replies the
  runner read; the judge command is always shown in full. `aggregate` counts voids in its
  "rejected or void" line.
- `SECURITY.md` covers the judge runner and the MCP server.
- A run frozen before 0.3.0 has no `[judge]` section and stays in host mode, and keeps
  `transcript.handback_tool = ""`. In a host's auto mode, where the judge answers through
  `SubagentHandback`, such a run rejects every judge it records: start a new run (or freeze one
  with `[transcript] handback_tool = "SubagentHandback"`).
- Through MCP, the read-only tools (`status`, `report`, `rubrics_*`) read any locked run, and
  `status` says whether the server serves it; the tools that change a run need the server's
  configuration to match it. A setting added in a later version counts as its default for an
  older run, so a new setting never locks one out; a 0.2.x run, whose `handback_tool` default
  changed, is changed through the command line or a server whose configuration sets it.
- A transcript root that overlaps the server's root (a host started from the home folder) is
  turned off instead of stopping the server, and `status` and every transcript refusal say
  why. A leading `~`, `$HOME` or `${HOME}` in a transcript root is the user's home. The plugin's
  transcript folder defaults to the host's own, so it works out of the box.
- Declared for macOS and Linux; Windows is not supported yet.

## [0.2.1] - 2026-11-04

### Fixed

- The configuration docs no longer say a punctuation mapping cannot add an ellipsis joint: any
  run of mapped and literal dots that normalises to an ellipsis reads as one, which a judge
  could write as `...` anyway. A test pins the behaviour, and the docs say why the built-in
  ellipsis entry stays (NFKC turns the character into `...` first; runs frozen with the entry
  stay valid).
- Every check but quote matching is pinned to the built-in normalisation by a test and a
  mutation: the re-roll hash, the previous version and the baseline riding along, and the
  one-quote flag.
- The library guard drops only the repository-local git variables, as
  `git rev-parse --local-env-vars` lists them, before asking git whether the library is
  ignored. It used to drop every
  `GIT_` variable, so a `GIT_CONFIG_GLOBAL` the user's own git obeys was ignored: the guard
  could refuse a library that git ignores, or accept one that git would let be committed.

## [0.2.0] - 2026-11-04

### Added

- A rubric library: eight built-in templates (cv, cover-letter, pitch, message, essay,
  interview-answer, plan-doc, prompt-output) with aliases and facts hints, checked against a
  template format, and a user-level library (`SEALED_PANEL_RUBRICS`, `[library] path`, or the
  platform's user data directory) whose templates override built-ins.
- `sealed-panel rubrics list`, `suggest` (by kind and alias, keywords only break ties),
  `show`, `use` (a spec skeleton with TODO markers that `init` refuses until filled),
  `promote` (side by side, typed confirmation or `--yes`, all or nothing) and `reject`;
  `sealed-panel learn RUN` and `sealed-panel feedback RUN`.
- A spec may record `template: {id, version, sha256}`, frozen by the rubric hash, and `init`
  records whether the dimensions were modified. A spec without it keeps its 0.1.0 hash.
- Library guardrails: never inside the package, never inside a git working tree that does
  not ignore it, and no relative path from the environment or the settings.
- Learning after a sealed panel: `judges_disagree` and `control_too_close` candidates, and
  `user_disagreement` from `feedback`, de-duplicated by kind, template version and dimension,
  with rejections remembered. Learning never fails a seal. Nothing changes a template without
  a person running `rubrics promote`, and a frozen run never changes.
- The control's median per dimension is stored in each round.

### Changed

- A run frozen by 0.1.0 keeps working: its configuration has no `[library]` section, and the
  library is resolved when a command needs it.

## [0.1.0] - 2026-11-04

### Added

- `sealed-panel` command with `init`, `brief`, `record`, `measure`, `aggregate`, `status` and
  `report`.
- Frozen, hashed rubric with 1/3/5 anchors, a validated spec shape, facts given as text or as a
  file frozen by content, declared choices that carry the commissioner's words.
- Blind briefs: labels, order and reading stances shuffled per judge, a control planted every
  round, an optional known-good, the previous version as a blind paired comparator.
- Reply validation: quotes checked by code, a severity per dimension that caps the score,
  control and known-good calibration per judge, at most two replacements per judge slot.
- Transcripts must carry the whole brief in their prompt, and added text is reported;
  `record --void` records a judge that never replied, with its own cap, but never fills a
  seat: every judge slot needs a real attempt. Replies returned through a configured handback
  tool are read.
- Stop rule (CONVERGED, PLATEAU, CAP) and a sealed final panel, once per run, that also scores
  the round-1 text.
- Every re-roll path through the CLI that this version knows of is refused, or flagged in the
  report where it cannot be checked; each refusal is covered by a test and a mutation.
- Settings in `defaults.toml` with hard bounds on every guard, overridable per run by the spec
  or by `init --config`, frozen into the run, and listed in the report when they differ from
  the defaults.
- Agent skill in `skill/`, synthetic example with demo judges in `examples/announcement/`.
- Mutation check, pre-commit configuration, CI and branch protection settings.

[Unreleased]: https://github.com/tdrapp/sealed-panel/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/tdrapp/sealed-panel/compare/5ec3e76...v0.4.0
[0.3.1]: https://github.com/tdrapp/sealed-panel/compare/92199c7...5ec3e76
[0.3.0]: https://github.com/tdrapp/sealed-panel/compare/v0.2.1...92199c7
[0.2.1]: https://github.com/tdrapp/sealed-panel/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tdrapp/sealed-panel/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tdrapp/sealed-panel/releases/tag/v0.1.0
