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
- The judge runner: a judge process that gets a variable outside `PATH`, `HOME`, `LANG` and
  `[judge] env_allow`, a reply recorded from a process that failed, timed out or passed the
  output cap, two judges on one seat, or a process started and never recorded.
- The MCP server: a tool call that picks the judge command, its environment or its timeout, a
  path that escapes the root, a re-roll the command line would refuse, or a rubric candidate
  promoted or rejected through a tool.

## The judge runner and the MCP server

A `[judge] command` executes code by design, with your rights. Whoever writes the configuration
chooses what runs, so treat that file like a script you would run, and keep credentials out of
it: the command is stored with every reply and shown next to the grade. Only the names in
`env_allow` reach the judge, and they reach it whole.

The caller of the MCP server is the author of the text: an agent under optimisation pressure,
not a trusted party. So the server takes the judge settings only from the file given to
`sealed-panel mcp --config`, never from a tool call, confines every path to its root, and leaves
promotion to a person at a terminal. It has no authentication: it serves one host over stdio.

What this does not defend: the judge command itself (a command that asks a model several times
and prints the best answer is a re-roll the runner cannot see; a process that leaves the
session survives the timeout); an author who can also edit the server's configuration or the
run's files; an agent with a shell, which can run the command line with a configuration of its
own, a choice the report then shows next to the grade. Anything the root holds can be read by
the caller as an artefact or a spec, so do not make it wider than the texts being graded.

## Out of scope

The bypasses listed under the threat model and in the section above, and the limits listed in
the README.

## Reporting a vulnerability

Report it privately through GitHub security advisories: open the repository's **Security** tab
and choose **Report a vulnerability**. Do not open a public issue.

Please include the commands or files that reproduce it and what an agent could gain from it.
You should get a first answer within a week.
