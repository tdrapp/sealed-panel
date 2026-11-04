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
- Markdown wraps at 96 columns, outside code blocks and tables; re-wrap the whole paragraph
  after an edit. `tests/test_docs.py` checks the width.
- Examples are synthetic. Do not commit real CVs, messages or other personal material.
- Code: typed (mypy strict on `src`, `tools` and `examples`), formatted by ruff, standard
  library only at runtime. A setting goes in `defaults.toml` and `docs/configuration.md`; a
  setting that guards the protocol also gets a hard bound in `config.py`. The brief texts stay
  in `brief.py`: they are the instrument.

## Maintainers

Branch protection for `main` is kept in `.github/branch-protection.json` (required checks,
strict status, pull request required, linear history, no force push, no deletion, resolved
conversations). Apply it with:

```sh
gh api -X PUT repos/tdrapp/sealed-panel/branches/main/protection --input .github/branch-protection.json
```

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Do not open a public issue.
