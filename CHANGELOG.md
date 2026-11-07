# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- A run frozen before 0.3.0 has no `[judge]` section and stays in host mode.

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

[Unreleased]: https://github.com/tdrapp/sealed-panel/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/tdrapp/sealed-panel/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/tdrapp/sealed-panel/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/tdrapp/sealed-panel/releases/tag/v0.1.0
