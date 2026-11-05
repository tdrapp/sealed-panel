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

## Agent host plugin

The repository is also a plugin, and its own marketplace, for agent hosts that install plugins
from a git repository. The plugin manifest and the marketplace file sit in the plugin folder
at the repository root; the plugin brings three things:

| Part | File | What it does |
|---|---|---|
| MCP server | `.mcp.json` | starts `uvx --from 'sealed-panel[mcp]==0.3.0' sealed-panel mcp` |
| Skill | `skills/sealed-panel/` | the procedure the agent follows, a copy of `skill/` |
| Judge agent | `agents/eval-judge.md` | the judge instructions of `skill/judge.md`, one fresh instance per brief |

Install it from your host's prompt:

```
/plugin marketplace add tdrapp/sealed-panel
/plugin install sealed-panel@sealed-panel
```

The plugin needs `uvx` on the path. The judge agent may only read files, and its
instructions forbid using any tool: give it one brief's text as its whole prompt. A judge that
can read files could open the run directory, so never pass it a path.

To work on the plugin from a clone, point the server at the clone instead of the published
package, in your host's MCP configuration:

```json
{
  "mcpServers": {
    "sealed-panel": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/sealed-panel", "--extra", "mcp", "sealed-panel", "mcp"]
    }
  }
}
```

Then start your host with its option for a local plugin directory pointing at the clone.

`skill/` is the one source of the skill. `skills/sealed-panel/` and
`.agents/skills/sealed-panel/` are copies that hosts read, and `tests/test_packaging.py` fails
when a copy drifts: after editing `skill/`, copy it over both.

## Agent hosts that read `.agents/skills`

Some agent hosts read skills from `.agents/skills/` in the repository and MCP servers from a
TOML configuration file. The skill is there already (`.agents/skills/sealed-panel/`, a copy of
`skill/`, with its `name` and `description` front matter). Add the server to your host's
`config.toml`:

```toml
[mcp_servers.sealed-panel]
command = "uvx"
args = ["--from", "sealed-panel[mcp]==0.3.0", "sealed-panel", "mcp"]
tool_timeout_sec = 300
env_vars = ["MY_MODEL_API_KEY"]
```

`tool_timeout_sec` is raised to 300 because a `judge` call runs one judge after another and a
judge can take a minute, while such hosts commonly stop a tool call after 60 seconds. Split a
large panel across calls with the tool's `judges` parameter so that each call fits.

`env_vars` is the host's allow-list of variables passed to the server: list only what the judge
command needs (your model's key, if the runner calls a model), or drop the line in host mode.
The judge command still sees only `PATH`, `HOME`, `LANG` and the names in `[judge] env_allow`,
so a key reaches a judge only when both lists name it.
