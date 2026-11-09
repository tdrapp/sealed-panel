# Contributing

Thank you for taking the time. sealed-panel is small on purpose: one protocol, enforced by
code, with a test for every refusal. Changes that keep it that way are welcome.

## Setup

You need Python 3.11 or later and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/tdrapp/sealed-panel
cd sealed-panel
uv sync --locked --python 3.12
.venv/bin/pre-commit install
```

`uv sync --locked` installs the package and the `dev` dependency group into `.venv`, at the
versions `uv.lock` pins, and fails if the lock no longer matches `pyproject.toml`. The
development tools live in `[dependency-groups]`: `test` (what the tests need), `dev` (`test`
plus the linters, the type checker and the MCP server's package) and `audit` (the vulnerability
audit). After changing a dependency, run `uv lock` and commit `uv.lock` with it. The `mcp` extra
stays for users who install the package.

`pre-commit install` installs two hooks: on commit (ruff lint and format, mypy, file
checks, and gitleaks on what is staged) and on push (the test suite).

## Checks

```sh
.venv/bin/pre-commit run --all-files
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/python tools/mutation_check.py --jobs 4
uv run --no-sync actionlint
uv run --no-sync lint-imports
uv run --no-sync xenon --max-absolute F --max-modules D --max-average B src tools examples
uv run --no-sync python tools/complexity_check.py
uv run --no-sync interrogate src
uv export --locked --all-extras --all-groups --no-emit-project -o audit.txt
uv run --locked --only-group audit pip-audit --strict --require-hashes --disable-pip -r audit.txt
```

CI runs the same checks, one job each. Every job but `pip-audit` is a required check in
`.github/branch-protection.json`: a vulnerability published tomorrow must not block an unrelated
pull request, so a red `pip-audit` is fixed in its own pull request.

| Job | What it checks |
|---|---|
| `lint`, `types` | ruff (lint, format, the security rules, docstrings) and mypy strict |
| `test (3.11)` to `test (3.13)` | the test suite with the `mcp` extra, and the coverage floor |
| `test (3.11, core)` to `test (3.13, core)` | the test suite without the extra: the MCP tests skip |
| `mutation` | every seeded defect of `tools/mutation_check.py` is caught |
| `build` | the sdist and the wheel build; the wheel installs in a fresh environment with the lock's versions, `sealed-panel --version` names the package version, and the MCP server answers `initialize` (`tools/smoke_mcp.py`) |
| `actionlint` | the workflows, with shellcheck on each step's script |
| `gitleaks` | secrets in the whole history, with a pinned release checked by its sha256; `.gitleaks.toml` allowlists only the sha256 digests of the test fixtures and the lock |
| `imports` | the import contract in `pyproject.toml`: the command line and the MCP server above the core, `store` and `errors` at the bottom; a new module must be placed in a layer |
| `complexity` | xenon caps the ranks at their 0.3.1 level (worst block F, worst module D, average B), and `tools/complexity_check.py` holds every function above rank B at its complexity in `tools/complexity_baseline.json`; a new function stays at rank B (complexity 10 or less) |
| `docstrings` | interrogate: at least 80% of the public functions, classes and modules of the package have a docstring |
| `pip-audit` | known vulnerabilities in every locked package, by hash |

When a refactor lowers a function's complexity, run `tools/complexity_check.py --update` and
commit the lower baseline; never raise the baseline to let a change in.

When you add or change a refusal, add a test that fails without it, and add a mutation to
`tools/mutation_check.py` that switches it off. The mutation check must kill it.

## The mutation check

Each mutation in `tools/mutation_check.py` is an exact text and its replacement. The tool copies
`src`, `tests` and `examples` to a temporary directory, applies one mutation, runs pytest with
`-x` on the copy, and deletes the copy. pytest's exit code is the verdict: 0 is a survivor, 1 (a
test failed) is a kill, and 2 (interrupted) is a kill flagged INTERRUPT: a mutation that lets
ctrl-c reach pytest, or breaks an import, ends the run that way. 3 (internal error), 4 (usage
error, such as a test id the copy lacks) and 5 (no test collected) say the run itself went
wrong: an error that fails the check.

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

A run past its time limit, max(60 s, 5 x the selected tests' time + 30 s), is killed with its
process group. A timeout of the selected tests counts as detected only after one more run on its
own, and only if the same tests then pass within the same limit on unmutated code; otherwise it
is an error, and a second run that does not time out gives its own verdict. A timeout of the
whole-suite run of a survivor is always an error: the selected tests passed and the other tests
never execute the changed line, so the timeout says the machine is slow, not that the mutation
is caught. Processes a test starts in a session of their own outlive the kill; on a throwaway CI
runner that is harmless.

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
  library only at runtime. Every public module, class and function of the package has a
  docstring (ruff `D1`). Each function of the public API (`sealed_panel.__all__`) has an `Args:`
  section (`tests/test_docstrings.py`), and an `Args:` section anywhere names every argument
  (ruff `D417`); give a new public function one. A setting goes in `defaults.toml` and
  `docs/configuration.md`; a setting that guards the protocol also gets a hard bound in
  `config.py`. The brief texts stay in `brief.py`: they are the instrument.

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
