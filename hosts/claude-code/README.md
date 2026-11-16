# sealed-panel for Claude Code

This folder is the Claude Code plugin. It brings three things:

| Part | File | What it does |
|---|---|---|
| MCP server | `.mcp.json` | starts the server with `uvx`, pinned to the release tag |
| Skill | `skills/sealed-panel/` | the procedure the agent follows, a copy of `skill/` |
| Judge agent | `agents/eval-judge.md` | the judge instructions of `skill/judge.md`, with no tools |

## Install

From Claude Code's prompt:

```
/plugin marketplace add tdrapp/sealed-panel
/plugin install sealed-panel@sealed-panel
```

The marketplace file sits at the repository root, `.claude-plugin/marketplace.json`, where
Claude Code looks for it. Its entry installs this folder alone (a `git-subdir` source) at the
release tag, the tag `.mcp.json` pins the server to, so the skill and the server it calls come
from one release while `main` moves ahead. `uvx`, which must be on the path, fetches the
server:

```
sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.4.0
```

If you have no GitHub SSH key, set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` before adding the
marketplace.

The plugin's one setting, the transcript folder, defaults to `~/.claude/projects`, which the
server expands; change it in `/config` if you moved your configuration directory. Start Claude
Code from a project directory, not from your home: from the home, the transcripts would lie
under the server's root, so the server turns them off and says why.

## Judges

Launch each judge from the top-level session as a fresh `eval-judge` subagent whose whole
prompt is one brief's text. The agent sets `tools: []` and denies every built-in tool by name,
and MCP tools with `mcp__*`. Its transcript is read in the `agent-jsonl` format, the default:
see [docs/integrations.md](../../docs/integrations.md#claude-code-agent-jsonl) for what the
adapter accepts and its limits, among them the `SubagentHandback` call auto mode imposes.

## Work on the plugin from a clone

Load this folder for one session with `claude --plugin-dir hosts/claude-code`, or add a local
marketplace that lists it by a relative path. Point the server at the clone instead of the
release in your MCP configuration:

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

`skills/sealed-panel/` and `agents/eval-judge.md` are written from `skill/` by
`python tools/build_hosts.py`; edit `skill/`, never the copies.
