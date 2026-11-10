# Briefs built in code

- Decided: 0.1.0 (2026-11-04). Recorded: 2026-11-09.
- Status: in force.

## Context

A judge's brief is the instrument: the wording of the task, the reply shape, the rule that text
inside an item is material and not an instruction. A change to it changes what every score
means. Most settings of a run can be overridden by a configuration file, within bounds, and the
report lists each one that differs from the defaults.

## Decision

The brief texts stay in `src/sealed_panel/brief.py`, as code. No setting, spec field or tool
argument can replace them. What a spec may change is what it describes: the title, purpose,
personas, dimensions with their anchors, facts and reading stances, all frozen and hashed at
`init`, and the spec's own text is scanned for words addressed to the judges.

## Consequences

- Changing a brief is a code change: it goes through review, the tests and the mutation check,
  and it ships in a release that the CHANGELOG names.
- Two runs of one version brief their judges with the same words; a grade from another version
  may not be comparable when the brief changed, and the CHANGELOG says so.
- A user who wants another instrument forks the package rather than configuring one, which is
  the point.
