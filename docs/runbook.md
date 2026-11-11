# Runbook

Last reviewed: 2026-11-09, for 0.3.1.

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

The first release, in this order:

1. Make the repository public: the plugin marketplace and the git install need it.
2. On the index, add a pending trusted publisher for this repository with the workflow
   `release.yml` and the environment `pypi`. In the repository settings, create the `pypi`
   environment with a required reviewer, before any tag.
3. In a release commit, move `[Unreleased]` in `CHANGELOG.md` to `[0.3.0]` with its compare
   link. (Done: `92199c7`.)
4. Tag `v0.1.0` on `0d1bb9e`, `v0.2.0` on `bf103bb` and `v0.2.1` on `a7ba6dc`, and push them
   together: none of these commits holds `release.yml`, so nothing publishes.
5. Tag the release commit of step 3 `v0.3.0` and push it alone: GitHub creates no event when
   more than three tags are pushed at once.
6. Once `v0.3.0` is published, tag the 0.3.1 release commit `v0.3.1` and push it alone, as a
   later release.

A later release: move the `Unreleased` entries of `CHANGELOG.md` under the new version, set the
version in `pyproject.toml`, `src/sealed_panel/__init__.py`, the plugin manifest and marketplace
file (its `version` and the `ref` of its plugin source, the tag), `.mcp.json`, the README's
version line and the install lines (`tests/test_packaging.py` checks they agree), merge, and
push a tag `v<version>`. The workflow checks that the tag names the package version, builds the
sdist and the wheel, and publishes them.

After the first publish, the docs' index lines apply: the hint `sealed-panel mcp` prints without
the extra (`cli.py`) may then name the index. Until then it names the tag, because an agent
reading it would install whatever holds that free name. `.mcp.json` may then move from the git
form to `sealed-panel[mcp]==<version>`, and `tests/test_packaging.py` moves with it.

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
- A machine that went away mid-run: `status` reads the ledger and says what comes next.

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
