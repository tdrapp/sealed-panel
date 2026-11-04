# Security policy

## Supported versions

Only the latest release receives fixes.

## Threat model

sealed-panel defends a grade against an agent under optimisation pressure that drives the CLI:
every re-roll path through the CLI that this version knows of is refused, or flagged in the
report where it cannot be checked. It does not defend against someone who edits the run's
files by hand, starts a new run directory, edits a spec together with its
lock, or deletes a transcript before aggregating. Replies recorded with `--output` cannot be
checked and are flagged in the report.

## In scope

- A sequence of documented CLI commands that records a second grade for a run, counts a reply
  that should be refused, or hides a judge that was briefed.
- A configuration value that switches off a guard without being refused or disclosed.
- A crafted spec, text, reply or transcript that executes code, or reads or writes outside the
  run directory and the files given on the command line.

## Out of scope

The bypasses listed under the threat model, and the limits listed in the README.

## Reporting a vulnerability

Report it privately through GitHub security advisories: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue.

Please include the commands or files that reproduce it and what an agent could gain from it.
You should get a first answer within a week.
