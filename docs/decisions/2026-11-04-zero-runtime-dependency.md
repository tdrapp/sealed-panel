# Zero runtime dependency

- Decided: 0.1.0 (2026-11-04). Recorded: 2026-11-09.
- Status: in force.

## Context

sealed-panel decides which judge reads a text and whether a reply counts. Whatever the package
imports at runtime runs inside that decision, and every dependency is code that can change
under it between two installs: a new version, a compromised release, a changed default.

## Decision

The package runs on the Python standard library alone: `dependencies = []` in
`pyproject.toml`. What needs a third-party package is an optional extra, imported only by the
code that needs it: the MCP server is the `mcp` extra, imported when `sealed-panel mcp` starts.
Development tools live in dependency groups and are locked in `uv.lock`; they never reach a
user's install.

## Consequences

- The core reads TOML with `tomllib`, so Python 3.11 is the floor.
- Some conveniences are written by hand (argument parsing with `argparse`, the judge runner's
  process handling), and their tests are ours to keep.
- CI runs the whole suite without the extra on every supported Python, and checks that the
  extra's package is absent there, so an import that slips into the core fails.
- An extra is reviewed like a dependency: pinned to a compatible range, locked, and audited by
  `pip-audit`.
