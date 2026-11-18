# sealed-panel for the Codex CLI

This folder holds what the Codex CLI reads:

| Part | File | What it does |
|---|---|---|
| Skill | `skills/sealed-panel/` | the procedure the agent follows, a copy of `skill/` |
| MCP server | `config.toml` | the `[mcp_servers.sealed-panel]` table that starts the server with `uvx` |

What this folder states about Codex comes from two sources, kept apart below. Measured: run
on 2026-11-16 with Codex CLI 0.163.0-alpha.11.1, without a login (`codex debug prompt-input`,
which prints what a session would show the model, and `codex mcp list` and `codex mcp get`).
Read: Codex's documentation as read on 2026-11-16; not run in a logged-in session.

## Install

Copy the skill from a clone checked out at the release tag into your user skill folder, then
add the table of `config.toml` to `~/.codex/config.toml`. Both come from the same release, and
the table starts the server of that release:

```sh
git clone --branch v0.5.0 https://github.com/tdrapp/sealed-panel
mkdir -p ~/.agents/skills
cp -R sealed-panel/hosts/codex/skills/sealed-panel ~/.agents/skills/
```

```
sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.5.0
```

Start Codex from a project directory, not from your home folder. The table gives the server
`--transcript-root ~/.codex/sessions`, the folder where Codex writes its sessions (the server
expands `~`), and no `--root`, so the server's root is the folder it starts in. When the two
overlap, as they do if that folder is your home, the server turns transcripts off and says
why in `status` and in every refusal: `record` then refuses transcripts, and only a pasted
reply, flagged in the report, is left in host mode. Adding `"--root", "/path/to/texts"` to the
`args` fixes the root whatever the folder.

Measured:

- `sealed-panel` is listed from `~/.agents/skills/sealed-panel/`, as a copy and as a symbolic
  link to a copy, and is not listed from a clone of this repository since 0.5.0 (the root no
  longer holds `.agents/skills/`).
- `codex mcp list` and `codex mcp get sealed-panel` read the table: the command, its arguments,
  `startup_timeout_sec` 120, `tool_timeout_sec` 300 and the empty `env_vars`.

Read in Codex's documentation:

- Codex reads skills from `.agents/skills/` in every folder from the one it starts in up to
  the repository root, and from `~/.agents/skills/` for the user, and follows a symbolic link
  to a skill folder; `/skills` lists them in a session. Restart Codex after a change.
- A trusted project's `.codex/config.toml` may hold the table instead of `~/.codex/config.toml`.
- Codex waits 10 seconds for a server to start and 60 seconds for a tool call by default. The
  table raises the first to 120, since the first start fetches the package (how long that
  takes was not measured), and the second to 300, because a `judge` call runs one judge after
  another; split a large panel across calls with the tool's `judges` parameter.
- `env_vars` lists the variables Codex passes to the server: only what a judge command needs
  (your model's key, when the runner calls a model), none in host mode.

## Judges

Codex transcripts are read in the `rollout-jsonl` format: set `format = "rollout-jsonl"` under
`[transcript]` in the file given to `init --config`. Start each judge as a fresh session, from
an empty directory, with one brief's text as its whole prompt, and keep it to one turn. No way
to start a Codex session without tools is known; the adapter rejects any tool call the
transcript shows. See [docs/integrations.md](../../docs/integrations.md#codex-cli-rollout-jsonl)
for what the adapter accepts and its limits.

`skills/sealed-panel/` is written from `skill/` by `python tools/build_hosts.py`; edit
`skill/`, never the copy.
