# The judge command comes only from the server's configuration

- Decided: 0.3.0 (2026-11-07). Recorded: 2026-11-09.
- Status: in force.

## Context

With a judge command, the MCP server runs the judges itself. Whoever picks that command picks
the judge, and the caller of a tool is the author of the text: letting it choose the judge, its
environment or its timeout would let it grade itself.

## Decision

The judge settings come only from the file given to `sealed-panel mcp --config`, read once at
startup and refused when it lies under the server's root, where the caller could rewrite it. No
tool takes a command, an environment, a timeout or a configuration. The `init` tool freezes the
server's settings into every run it starts, and every tool that changes a run refuses one frozen
with other settings.

## Consequences

- To change the judge, the user restarts the server with another configuration; a run started
  under one judge is never finished under another through the tools.
- The command is frozen into the run and shown in full next to the grade. Since 0.3.1 the
  report also flags a round whose attempts ran under different values of an allowed
  environment variable, so the model belongs in the command, not in the environment.
- An agent with a shell can still run the command line with a configuration of its own; the
  report then shows that configuration next to the grade (`SECURITY.md`).
