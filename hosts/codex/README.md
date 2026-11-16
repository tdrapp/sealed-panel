# sealed-panel for the Codex CLI

This folder holds what the Codex CLI reads:

| Part | File | What it does |
|---|---|---|
| Skill | `skills/sealed-panel/` | the procedure the agent follows, a copy of `skill/` |
| MCP server | `config.toml` | the `[mcp_servers.sealed-panel]` table that starts the server with `uvx` |

## Install

Codex reads skills from `.agents/skills/` in every folder from the one it starts in up to the
repository root, and from `~/.agents/skills/` for your user; it follows a symbolic link to a
skill folder. Copy the skill (or link it) from a clone checked out at the release tag:

```sh
git clone --branch v0.4.0 https://github.com/tdrapp/sealed-panel
mkdir -p ~/.agents/skills
cp -R sealed-panel/hosts/codex/skills/sealed-panel ~/.agents/skills/
```

Then add the table of `config.toml` to `~/.codex/config.toml` (or to a trusted project's
`.codex/config.toml`). It starts the server of the same release:

```
sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.4.0
```

Restart Codex; `/skills` lists `sealed-panel` and `codex mcp list` the server.

`--transcript-root ~/.codex/sessions` is the folder where Codex writes its sessions; the server
expands `~`. `tool_timeout_sec` is raised to 300 because a `judge` call runs one judge after
another and Codex stops a tool call after 60 seconds by default; split a large panel across
calls with the tool's `judges` parameter. `env_vars` lists the variables passed to the server:
only what a judge command needs, or none in host mode.

## Judges

Codex transcripts are read in the `rollout-jsonl` format: set `format = "rollout-jsonl"` under
`[transcript]` in the file given to `init --config`. Start each judge as a fresh session, from
an empty directory, with one brief's text as its whole prompt, and keep it to one turn. No way
to start a Codex session without tools is known; the adapter rejects any tool call the
transcript shows. See [docs/integrations.md](../../docs/integrations.md#codex-cli-rollout-jsonl)
for what the adapter accepts and its limits.

`skills/sealed-panel/` is written from `skill/` by `python tools/build_hosts.py`; edit
`skill/`, never the copy.
