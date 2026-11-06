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
.venv/bin/python tools/mutation_check.py
```

CI runs the same: lint, types, tests on Python 3.11 to 3.13, and the mutation check.

When you add or change a refusal, add a test that fails without it, and add a mutation to
`tools/mutation_check.py` that switches it off. The mutation check must kill it.

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

Releases go to the package index through trusted publishing, so no token exists anywhere.
Once, on the index, add a pending trusted publisher for this repository with the workflow
`release.yml` and the environment `pypi`, and create that environment in the repository
settings, with a required reviewer. Then, to release: move the `Unreleased` entries of
`CHANGELOG.md` under the new version, set the version in `pyproject.toml`,
`src/sealed_panel/__init__.py`, the plugin manifest and marketplace file, `.mcp.json` and the
install lines (`tests/test_packaging.py` checks they agree), merge, and push a tag
`v<version>`. The workflow checks that the tag names the package version, builds the sdist and
the wheel, and publishes them. After the first publish, the docs' index lines apply; `.mcp.json`
may then move from the git form to `sealed-panel[mcp]==<version>`.

Branch protection for `main` is kept in `.github/branch-protection.json` (required checks,
strict status, pull request required, linear history, no force push, no deletion, resolved
conversations). Apply it with:

```sh
gh api -X PUT repos/tdrapp/sealed-panel/branches/main/protection --input .github/branch-protection.json
```

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Do not open a public issue.
