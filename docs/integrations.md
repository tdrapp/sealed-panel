# Integrations

sealed-panel runs the same protocol three ways: from the command line, as MCP tools for an
agent host, and as a Python library in a pipeline. In each, the judges are either run by your
agent host (host mode: you launch a fresh judge per brief and record its reply) or by the judge
runner (a command you configure, started once per brief; see `sealed-panel judge` in
[cli.md](cli.md)).

## Who runs the judges

| Mode | Set up | Proof that a judge got the brief |
|---|---|---|
| Host mode | `[judge] command` empty (the default) | the judge's transcript (`record --transcript`), or none for a pasted reply |
| Runner | `[judge] command` in the file given to `init --config` | sealed-panel wrote the brief and read the reply itself |

MCP sampling, where the server asks the host's model to answer, is not used: the MCP
specification deprecates it, and the agent hosts we checked do not support it. Roots and server
logging are deprecated too; paths come from the tool parameters, and logs go to stderr.

## MCP server

Install the extra and start the server over stdio:

```sh
pip install 'sealed-panel[mcp]'
sealed-panel mcp
```

Or let `uvx` fetch it on demand:

```sh
uvx --from 'sealed-panel[mcp]==0.3.0' sealed-panel mcp
```

The tools are listed in [cli.md](cli.md#mcp). Relative paths resolve against the directory the
server starts in, or `--root DIR`. To let the server run the judges, pass a configuration with
a `[judge] command`, either to the `init` tool (`config`) or to the server
(`sealed-panel mcp --config judge.toml`), which `init` then uses when a call names none.
