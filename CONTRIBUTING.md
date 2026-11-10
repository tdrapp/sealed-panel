# Contributing

Thank you for taking the time. sealed-panel is small on purpose: one protocol, enforced by
code, with a test for every refusal. Changes that keep it that way are welcome.

## Setup

You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/) (or plain `pip` and
`venv`).

```sh
git clone https://github.com/tdrapp/sealed-panel
cd sealed-panel
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev]'
.venv/bin/pre-commit install
```

`pre-commit install` installs two hooks: on commit (ruff lint and format, mypy, file
checks) and on push (the test suite).

## Checks

```sh
.venv/bin/pre-commit run --all-files
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/python tools/mutation_check.py --jobs 4
```

CI runs the same: lint, types, tests on Python 3.11 to 3.13, and the mutation check.

When you add or change a refusal, add a test that fails without it, and add a mutation to
`tools/mutation_check.py` that switches it off. The mutation check must kill it.

## The mutation check

Each mutation in `tools/mutation_check.py` is an exact text and its replacement. The tool copies
`src`, `tests` and `examples` to a temporary directory, applies one mutation, runs pytest with
`-x` on the copy, and deletes the copy. pytest's exit code is the verdict: 0 is a survivor, 1 (a
test failed) is a kill, and 2 (interrupted) is a kill flagged INTERRUPT: a mutation that lets
ctrl-c reach pytest, or breaks an import, ends the run that way. 3 (internal error), 4 (usage
error, such as a test id the copy lacks) and 5 (no test collected) say the run itself went
wrong: an error that fails the check. A run past its time limit is killed with its process group
and counts as detected, after one more run on its own.

Running the whole suite on every mutation took about an hour in CI, so the tool selects tests
first. It runs the unmutated suite once under coverage, with one context per test, and maps
each mutation's lines to the tests that execute them. A mutation runs only those tests,
cheapest first. A mutation outside Python (`defaults.toml`), or on lines that run only at
import, gets the whole suite. The map is built in each run, never cached, so it always matches
the code under test.

A test that does not execute a line cannot see a change to it, so a kill from the selection is a
real kill, provided the selection itself passes without the mutation. That is checked for each
kill: the selected tests up to the one that failed (all of them after an interrupt) must pass,
in the same order, on unmutated code, or the run fails. A survivor is different: the selection
could have missed a test, so a mutation the selection does not kill runs again on the whole
suite, and is reported as a survivor only if the whole suite misses it too.

```sh
.venv/bin/python tools/mutation_check.py                    # all mutations, one at a time
.venv/bin/python tools/mutation_check.py --jobs 4           # all mutations, four at once
.venv/bin/python tools/mutation_check.py --only "control check off"
.venv/bin/python tools/mutation_check.py --jobs 2 --shard 1/4 --json shard-1.json
.venv/bin/python tools/mutation_check.py --merge shard-*.json
.venv/bin/python tools/mutation_check.py --full-suite --jobs 4
```

A shard holds the mutations whose name hashes to it, so shards never overlap and together
cover the list. `--merge` fails when a mutation is missing or appears twice, then applies the
rule: every mutation detected, except the expected survivors, which must survive.
`--full-suite` skips the selection and runs the whole suite on each mutation.

CI runs every mutation on every pull request, in four shards of two workers each, and the
`mutation` check merges their reports. A scheduled workflow runs the whole suite on every
mutation each night, so a fault in the selection shows within a day.

## Branches and pull requests

1. Branch from `main`: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
2. Keep a pull request to one logical change. Update `docs/` and the `Unreleased` section of
   `CHANGELOG.md` in the same pull request.
3. Open the pull request with the template filled in. CI must be green.
4. Pull requests are merged with squash, so the title becomes the commit on `main`.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
`test:`, `ci:`, `build:`, `chore:`, `refactor:`. Imperative mood, a subject under 72
characters, and a short body when the reason is not obvious from the subject.

```
fix: refuse a known-good identical to the candidate

A known-good equal to the candidate made the calibration check pass by construction.
```

## Style

- Plain ASCII punctuation in docs: commas, colons, parentheses, straight quotes.
- Sober prose: short sentences, no superlatives, no marketing words.
- Markdown wraps at 96 columns; re-wrap the whole paragraph after an edit.
  `tests/test_docs.py` checks the width and exempts code blocks, tables, front matter, lines
  with no space (a badge, a long link), `tests/fixtures/`, and the judged example texts under
  `examples/announcement/` (`control.md`, `draft_v1.md`, `facts.md`): those are texts a panel
  scores, kept as written, so never re-wrap them.
- Examples are synthetic. Do not commit real CVs, messages or other personal material.
- Code: typed (mypy strict on `src`, `tools` and `examples`), formatted by ruff, standard
  library only at runtime. A setting goes in `defaults.toml` and `docs/configuration.md`; a
  setting that guards the protocol also gets a hard bound in `config.py`. The brief texts stay
  in `brief.py`: they are the instrument.

## Vendor names

The project stays vendor-neutral: docs, code and commit messages say "a model CLI" or "your
agent host". One path is the single accepted exception: the plugin folder at the repository
root, whose name the host requires.

## Maintainers

Releases go to the package index through trusted publishing, so no token exists anywhere. The
workflows pin every action by commit, with the version in a trailing comment that Dependabot
keeps up to date.

The first release, in this order:

1. Make the repository public: the plugin marketplace and the git install need it.
2. On the index, add a pending trusted publisher for this repository with the workflow
   `release.yml` and the environment `pypi`. In the repository settings, create the `pypi`
   environment with a required reviewer, before any tag.
3. In a release commit, move `[Unreleased]` in `CHANGELOG.md` to `[0.3.0]` with its compare
   link.
4. Tag `v0.1.0` on `0d1bb9e`, `v0.2.0` on `bf103bb` and `v0.2.1` on `a7ba6dc`, and push them
   together: none of these commits holds `release.yml`, so nothing publishes.
5. Tag the release commit of step 3 `v0.3.0` and push it alone: GitHub creates no event when
   more than three tags are pushed at once.

A later release: move the `Unreleased` entries of `CHANGELOG.md` under the new version, set the
version in `pyproject.toml`, `src/sealed_panel/__init__.py`, the plugin manifest and marketplace
file, `.mcp.json` and the install lines (`tests/test_packaging.py` checks they agree), merge,
and push a tag `v<version>`. The workflow checks that the tag names the package version, builds
the sdist and the wheel, and publishes them.

After the first publish, the docs' index lines apply: the
hint `sealed-panel mcp` prints without the extra (`cli.py`) may then name the index. Until then
it names the tag, because an agent reading it would install whatever holds that free name.
`.mcp.json` may then move from the git form to `sealed-panel[mcp]==<version>`, and
`tests/test_packaging.py` moves with it.

Branch protection for `main` is kept in `.github/branch-protection.json` (required checks,
strict status, pull request required, linear history, no force push, no deletion, resolved
conversations). Apply it with:

```sh
gh api -X PUT repos/tdrapp/sealed-panel/branches/main/protection --input .github/branch-protection.json
```

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Do not open a public issue.
