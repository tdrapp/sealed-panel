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
| Runner | `[judge] command` in the file given to `init --config`, or to `sealed-panel mcp --config` | sealed-panel wrote the brief and read the reply itself |

MCP sampling, where the server asks the host's model to answer, is not used: the MCP
specification deprecates it, and the agent hosts we checked do not support it. Roots and server
logging are deprecated too; paths come from the tool parameters, and logs go to stderr.

## MCP server

Install the extra and start the server over stdio:

```sh
pip install 'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.3.0'
sealed-panel mcp
```

Or let `uvx` fetch it on demand:

```sh
uvx --from 'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.3.0' sealed-panel mcp
```

Once the package is published on the index, `pip install 'sealed-panel[mcp]'` and
`uvx --from 'sealed-panel[mcp]==0.3.0' sealed-panel mcp` do the same. Pin a `uvx` line to an
exact version or tag: an unpinned one runs whatever is newest the day it starts.

The tools are listed in [cli.md](cli.md#mcp). Every path a tool takes must resolve under the
root, symbolic links followed: `--root DIR`, else `[mcp] root` in the configuration, else the
directory the server starts in.

To let the server run the judges, start it with a configuration that holds a `[judge] command`,
kept outside the root: `sealed-panel mcp --root ~/texts --config ~/.config/my-judge.toml`. The
server reads that file once at startup and refuses to start when it lies under the root, since
the caller could rewrite it. It is the only source of the settings: the `init` tool freezes it
into every run and takes no configuration from the caller, and every tool that changes a run
refuses one frozen with other settings. The caller is the author of the text; letting it choose
the judge would let it grade itself.

To let `record` read your host's transcripts, name their folder with `--transcript-root DIR`
(repeat for several). It is read only and may not overlap the root: through MCP, a transcript is
accepted only there, with no symbolic link on the way, so a file the author wrote in its
workspace is no proof. An author with shell access to that folder can still forge one; only the
runner proves what the judge received. Without the option, `record` refuses transcripts, and a
pasted reply is the only way left in host mode, flagged in the report.

## Agent host plugin

The repository is also a plugin, and its own marketplace, for agent hosts that install plugins
from a git repository. The plugin manifest and the marketplace file sit in the plugin folder
at the repository root; the plugin brings three things:

| Part | File | What it does |
|---|---|---|
| MCP server | `.mcp.json` | starts the server with `uvx`, pinned to the release tag |
| Skill | `skills/sealed-panel/` | the procedure the agent follows, a copy of `skill/` |
| Judge agent | `agents/eval-judge.md` | the judge instructions of `skill/judge.md`, one fresh instance per brief |

Install it from your host's prompt:

```
/plugin marketplace add tdrapp/sealed-panel
/plugin install sealed-panel@sealed-panel
```

The plugin asks for one value when it is enabled, the transcript folder: the `projects` folder
of the host's configuration directory. It reaches the server as `--transcript-root`. Left empty,
the server starts without one. The plugin needs `uvx` on the path. The agent file sets `tools:
[]`. The host's reference does not document an empty list, but measured on its version 2.1.316,
it gives zero tools to a project agent and to a plugin agent, while leaving the field out would
grant every tool. If a later version refuses it, the agent fails to launch, loudly. The file
also denies every built-in tool by name, and MCP tools with `mcp__*`, as a second layer, and the
instructions forbid tools.

That is not quite no tool: in the host's auto mode a handback tool (`SubagentHandback`) is
imposed despite all this, and the judge's answer arrives through it instead of a final text.
That tool only returns the reply, and it is the default of `transcript.handback_tool`, so
`record` reads it. A judge that can read files could open the run directory and learn which
label is the candidate, so a reply whose transcript shows any other tool call is rejected by
`record`, and an agent with any tool call at all, whichever its name, counts as having replied
and cannot be voided. Give the judge one brief's text as its whole prompt, never a path.

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
args = ["--from", "sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.3.0", "sealed-panel", "mcp", "--transcript-root", "/path/to/your/host/sessions"]
tool_timeout_sec = 300
env_vars = ["MY_MODEL_API_KEY"]
```

`tool_timeout_sec` is raised to 300 because a `judge` call runs one judge after another and a
judge can take a minute, while such hosts commonly stop a tool call after 60 seconds. Split a
large panel across calls with the tool's `judges` parameter so that each call fits.

`--transcript-root` names the folder where this host writes its session logs, if they hold a
judge's prompt and reply in the shape described under `[transcript]` in
[configuration.md](configuration.md). Drop it if they do not: replies are then pasted, and
flagged. `env_vars` is the host's allow-list of variables passed to the server: list only what
the judge command needs (your model's key, if the runner calls a model), or drop the line in
host mode. The judge command still sees only `PATH`, `HOME`, `LANG` and the names in `[judge]
env_allow`, so a key reaches a judge only when both lists name it.

## API pipelines

In a pipeline, nothing hosts an agent: the judge runner calls your model. Write a judge command
that reads the brief on stdin and prints the model's answer on stdout, set it in `[judge]
command`, and name the variables it needs (your API key) in `[judge] env_allow`. Start from
`examples/runner/judge_template.py`, which reaches a model through a model command line or an
HTTP endpoint, both set by environment variables; adapt the one function that speaks to your
model's API. `examples/runner/judge.toml` is the matching configuration.

The same steps are a Python library: the functions the command line and the MCP server call.

```python
from pathlib import Path
from sealed_panel import aggregate_round, init_run, judge_round, load_config, render_report
from sealed_panel import write_briefs

run = Path("runs/q3-letter")
init_run(run, Path("spec.json"), load_config(Path("judge.toml")))
write_briefs(run, 1, Path("letter.md"), Path("control.md"), None, sealed=True)
judge_round(run, 1, sealed=True)  # one fresh process per brief; returns each result
aggregate_round(run, 1, sealed=True)
print(render_report(run))
```

`examples/runner/library_usage.py` is this one-shot grade as a script, with a rejected or void
judge replaced once. A loop run is the same calls with `sealed=False` and a round number,
revising from `RUN/feedback/after_rN.md` while `status(run)["decision"]` is `CONTINUE`. Every
refusal raises `EvalError` with its reason. An HTTP transport for the MCP server is not part of
this release.
