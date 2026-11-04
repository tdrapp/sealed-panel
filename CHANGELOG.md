# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/tdrapp/sealed-panel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tdrapp/sealed-panel/releases/tag/v0.1.0
