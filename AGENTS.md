# AGENTS.md

Instructions for any coding agent working in this repository. People read
[CONTRIBUTING.md](CONTRIBUTING.md); this file is the short form, with the mechanism that holds
each rule.

## What this is

sealed-panel grades a text with blind panels of LLM judges and a sealed final panel. The
package refuses: every way to re-roll a grade that it knows of is refused by code or flagged in
the report. A change that weakens a refusal is a change to the instrument, not a cleanup.

## Commands

```sh
uv sync --locked --python 3.12          # the package and the dev group, from uv.lock
.venv/bin/pre-commit install            # commit, commit-msg and push hooks
.venv/bin/pytest -m "not slow"          # the fast tier, in seconds
.venv/bin/pytest                        # every test, with the coverage floor
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/lint-imports
.venv/bin/python tools/complexity_check.py
.venv/bin/interrogate src
.venv/bin/python tools/mutation_check.py   # long: every seeded defect must be caught
```

A larger change starts from a plan in [docs/plans/](docs/plans/README.md). Release and
recovery steps are in [docs/runbook.md](docs/runbook.md); the reasons behind the fixed choices
are in [docs/decisions/](docs/decisions/).

## Hard rules

| Rule | What holds it |
|---|---|
| A refusal you add or change has a test that fails without it, and a mutation in `tools/mutation_check.py` that switches it off. Append to `MUTATIONS`; never weaken an existing entry. | `tools/mutation_check.py` (CI `mutation`); the README states its figures and `tests/test_docs.py` checks them |
| No runtime dependency: the package runs on the standard library; the MCP server is the optional `mcp` extra. | `dependencies = []` in `pyproject.toml`; CI `test (3.x, core)` runs without the extra and checks it is absent; [decision](docs/decisions/2026-11-04-zero-runtime-dependency.md) |
| The brief texts stay in `brief.py`, never in a setting: they are the instrument. | Review; [decision](docs/decisions/2026-11-04-briefs-built-in-code.md) |
| A setting goes in `defaults.toml` and `docs/configuration.md`; a setting that guards the protocol gets a hard bound in `config.py`. | `tests/test_config.py`; bounds covered by mutations |
| The MCP server speaks stdio only, and its judge command comes only from the configuration the server was started with, never from a tool call. | `tests/test_mcp.py`; [stdio](docs/decisions/2026-11-07-mcp-over-stdio-only.md) and [judge command](docs/decisions/2026-11-07-judge-command-from-server-config.md) decisions |
| An MCP tool's name, annotations or schemas change only on purpose. | `tests/test_mcp_schema.py` against `tests/mcp_tools.json` |
| `cli` and `mcp_server` sit above the core; `store` and `errors` at the bottom; a new module is placed in a layer. | import-linter contract in `pyproject.toml` (CI `imports`) |
| No function grows more complex; a new one stays at complexity 10 or less. | `tools/complexity_check.py` and xenon (CI `complexity`) |
| Every public module, class and function has a docstring; the public API's have an `Args:` section. | ruff `D1`, `D417`; `tests/test_docstrings.py`; interrogate (CI `docstrings`) |
| Typed code, ruff clean, including the security rules and no stray `print`. | mypy strict and ruff (CI `lint`, `types`, pre-commit) |
| Coverage of the package stays at 97% or more. | `--cov-fail-under` in `pyproject.toml` |
| A test that starts a process other than git is marked `slow`. | an autouse fixture in `tests/conftest.py`; `--strict-markers` |
| Dependencies change only through `uv lock`, and `uv.lock` is committed with the change. | `uv sync --locked` in every CI job |
| Every workflow action is pinned by commit, with its version in a comment. | `tests/test_packaging.py`; actionlint (CI) |
| The skill has one source, `skill/`; its copies stay equal. | `tests/test_packaging.py` |
| The version agrees across `pyproject.toml`, `__init__.py`, the plugin files, `.mcp.json`, the README and the install lines. | `tests/test_packaging.py` |
| No secret in the repository or its history. | gitleaks (pre-commit hook, CI `gitleaks`) |
| Commit messages: Conventional Commits, a subject under 72 characters, a blank line after it. | `tools/check_commit_msg.py` (commit-msg hook) |
| Markdown wraps at 96 columns (not the judged example texts nor `tests/fixtures/`). | `tests/test_docs.py` |
| A change lands with its docs and a line under `[Unreleased]` in `CHANGELOG.md`; released sections are never edited. | Review |
| Every pull request carries the verdict of an independent cold review, and its trade-offs. | The pull request template; review ([CONTRIBUTING](CONTRIBUTING.md)) |
| Examples are synthetic: no real CV, message or other personal text. | Review |

## Neutrality

The project is vendor-neutral. Code, docs, tests, examples and commit messages say "a model
CLI", "a model", "your agent host", never the name of a model vendor, a model, or an agent
host. The one exception is the files a host requires under its own name: the plugin folder at
the root and `.mcp.json`. Model ids in examples are placeholders such as `my-model-2026-01-15`.
Plain ASCII punctuation in prose: no long dashes, no curly quotes. A style check the
maintainer runs as a commit hook (kept outside the repository) refuses both; a reviewer checks
them on every pull request.
