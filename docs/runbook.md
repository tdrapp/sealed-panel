# Runbook

Last reviewed: 2026-11-18, for 0.5.0.

How to run sealed-panel, release it, and recover when something goes wrong. The reasons behind
each rule are in [protocol.md](protocol.md); every command and flag is in [cli.md](cli.md).

## Run

1. Write the spec (`sealed-panel rubrics use KIND --out spec.json` gives a skeleton), and give
   `init` a configuration when the judges are a command: `[judge] command` with the model in
   it, `env_allow` for the credential only, and `model_id` with a dated model id.
2. `sealed-panel init RUN --spec spec.json [--config judge.toml]`. A new run directory per
   text and rubric: a frozen run never changes.
3. Per round: `brief`, then the judges (`judge`, or fresh agents and `record`), then `measure`
   for each instrument, then `aggregate`, then `status`. Revise from
   `RUN/feedback/after_rN.md` while `status` says `CONTINUE`.
4. On `CONVERGED`, `PLATEAU` or `CAP`: `brief --sealed`, the judges, `measure --sealed`,
   `aggregate --sealed`, `report`. On `BLOCKED BY FLAG`, remove the text addressed to a judge
   and brief the next round, or start a new run.
5. Read the report's notes before quoting the grade: pasted replies, prompt extras, a judge
   environment that changed in a round, other runs beside this one.

The walk-through in the README runs these steps on a synthetic example.

## Release

Releases go to the package index through trusted publishing, so no token exists anywhere. The
workflows pin every action by commit, with the version in a trailing comment that Dependabot
keeps up to date.

No version before 0.4.0 was published to the package index. The tags `v0.1.0`, `v0.2.0` and
`v0.2.1` mark early commits and published nothing; 0.3.0 and 0.3.1 are development milestones,
neither tagged nor published (`CHANGELOG.md` names the commit that holds each). 0.4.0 is the
first published release.

The first release, 0.4.0, in this order:

1. The repository is public: the plugin marketplace and the git install need it.
2. Before the tag, on the index: a pending trusted publisher for this repository, naming the
   workflow `release.yml` and the environment `pypi`. In the repository settings, the `pypi`
   environment has a required reviewer and deploys only from tags `v*`.
3. Merge the release commit (the version set everywhere, as below).
4. Tag the merge commit `v0.4.0` and push that tag alone (`git push origin v0.4.0`): GitHub
   creates no event when more than three tags are pushed at once, and one tag per push keeps
   one release per run.
5. The `release` workflow checks that the tag names the package version and builds the sdist
   and the wheel; its publish job then waits for the `pypi` environment. In the Actions tab,
   open the run and approve the deployment: the job publishes through trusted publishing.
6. Create the GitHub release from the tag, with the release notes (what it is, the breaking
   changes, how to install from the tag).

A later release: move the `Unreleased` entries of `CHANGELOG.md` under the new version, with an
empty `[Unreleased]` above and both compare links at the bottom updated; set the version in
`pyproject.toml`, `src/sealed_panel/__init__.py`, the marketplace file at the root (its
`version` and the `ref` of its plugin source, the tag), the plugin manifest and `.mcp.json` in
the plugin's folder under `hosts/`, each host folder's `config.toml` and README, the README's
version line and the install lines in the README and `docs/integrations.md`
(`tests/test_packaging.py` checks they agree), and the `--version` test in `tests/test_cli.py`;
run `python tools/build_hosts.py --check`, then `uv lock`, and commit
`uv.lock` with the rest (CI runs `uv sync --locked`, which fails on a stale lock). Merge, then
tag the merge commit `v<version>` and push that tag alone. The workflow checks that the tag
names the package version and builds the sdist and the wheel; approve the `pypi` deployment in
the Actions tab, and it publishes them. Then create the GitHub release from the tag with the
release notes.

After the first publish, the docs' index lines apply: the hint `sealed-panel mcp` prints without
the extra (`cli.py`) may then name the index. Until then it names the tag, because an agent
reading it would install whatever holds that free name. The plugin's `.mcp.json` and each
host's `config.toml` may then move from the git form to `sealed-panel[mcp]==<version>`, and
`tests/test_packaging.py` moves with it.

Branch protection for `main` is kept in `.github/branch-protection.json` (required checks,
strict status, pull request required with no approving review, linear history, no force push,
no deletion, resolved conversations). Apply it with:

```sh
gh api -X PUT repos/tdrapp/sealed-panel/branches/main/protection --input .github/branch-protection.json
```

## Recovery

### A bad release: yank, then fix forward

The index never takes a file back under the same version, and a deleted version cannot be
uploaded again. So a broken or unsafe release is yanked, not deleted: on the index's page for
the release, with the reason (PEP 592). A yanked version stays installable when pinned exactly,
and resolvers skip it otherwise. Then:

1. Fix on a branch, with a test that fails on the broken behaviour.
2. Release the next patch version through the normal steps above.
3. In `CHANGELOG.md`, mark the yanked version `[x.y.z] - date [YANKED]` and say why in the
   next version's entry; the yanked section's text otherwise stays as released.
4. Leave the tag of the yanked version in place: it names what was published.

A security issue follows [SECURITY.md](../SECURITY.md) first.

### An interrupted run

Nothing is lost by stopping a command: every step writes whole files, and a run directory is
the record.

- `judge` interrupted (Ctrl-C, a closed terminal, a killed host): the judge that was running is
  recorded as a void and its started record removed. Run `sealed-panel judge RUN --round N`
  again: it judges the open seats only, each with a fresh process.
- A host-mode round where an agent received a brief and never replied: `aggregate` refuses the
  round while that agent is unrecorded. Record it with
  `sealed-panel record RUN --round N --judge J --agent-id ID --transcript FILE --void`, then
  brief a fresh judge on the same brief.
- `record` or `aggregate` stopped half-way: run it again. A reply already recorded is refused
  as a duplicate, and a round already aggregated is refused, so a second run cannot count
  anything twice.
- A machine that went away mid-run: `status` reads the run's rounds and says what comes next.
- A run created before 0.4.0, refused by every command: this version cannot continue a run
  created before 0.4.0: start a new run with this version, or finish the old one with the
  version that created it. Do not add a `run.json` by hand: 0.4.0 changed what a run freezes.
  A run of a later release's format names the releases to finish it with, each installed from
  its tag.

Never edit, move or delete a run's files by hand to get past a refusal: the refusals are the
protocol, and a hand edit is out of the threat model, not a way around it.

### A runner that died

A judge seat is held by a lock (`fcntl.flock` on `seat_j<J>.lock` beside the brief) while a
runner judges it; the kernel releases it when the runner's process dies, so nothing is ever
stale. Before its process starts, the runner writes `started_j<J>_<id>.json`; a runner killed
before recording leaves that file, and the next `judge` call, or `aggregate`, records the
attempt as a void, never a free seat (none on a seat that already holds a valid reply, none past
the void cap, none when the attempt it names is already stored; an unreadable record still gives
one). So the fix is to run `sealed-panel judge` again, never to delete the file:
deleting it hides a started attempt. "does not support file locks" means the run sits on a
filesystem without `flock` (some network mounts): move the run to a local disk.
