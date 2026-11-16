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
pip install 'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.4.0'
sealed-panel mcp
```

Or let `uvx` fetch it on demand:

```sh
uvx --from 'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v0.4.0' sealed-panel mcp
```

Once the package is published on the index, `pip install 'sealed-panel[mcp]'` and
`uvx --from 'sealed-panel[mcp]==0.4.0' sealed-panel mcp` do the same. Pin a `uvx` line to an
exact version or tag: an unpinned one runs whatever is newest the day it starts.

The ten tools are listed in [cli.md](cli.md#mcp); learning has none, and stays on the command
line. Every path a tool takes must resolve under the root, symbolic links followed: `--root
DIR`, else `[mcp] root` in the configuration, else the directory the server starts in. The
`init` tool refuses a spec that carries `injection_acknowledged`: acknowledging text addressed
to a judge decides whether a grade stands, so a person does it, with `sealed-panel init` at a
terminal.

To let the server run the judges, start it with a configuration that holds a `[judge] command`,
kept outside the root: `sealed-panel mcp --root ~/texts --config ~/.config/my-judge.toml`. The
server reads that file once at startup and refuses to start when it lies under the root, since
the caller could rewrite it. It is the only source of the settings: the `init` tool freezes it
into every run and takes no configuration from the caller, and every tool that changes a run
refuses one frozen with other settings. The caller is the author of the text; letting it choose
the judge would let it grade itself.

To let `record` read your host's transcripts, name their folder with `--transcript-root DIR`
(repeat for several). It is read only. One that overlaps the root is turned off, and `status`
says why: through MCP, a transcript is accepted only under a transcript root in use, its links
followed and refused unless it stays there, so a file the author wrote in its workspace is no
proof. An author
with shell access to that folder can still forge one; only the runner proves what the judge
received. Without the option, `record` refuses transcripts, and a pasted reply is the only way
left in host mode, flagged in the report.

## Agent hosts

Everything one agent host reads sits in its own folder under `hosts/`, with a README that says
how to install it. `skill/` is the one source of the skill: `python tools/build_hosts.py`
writes each host's copy of it (and the body of the plugin's judge agent, from
`skill/judge.md`), and `tests/test_packaging.py` fails when a copy drifts. Edit `skill/`,
never a copy.

| Host | Folder | Install | Transcript format |
|---|---|---|---|
| Claude Code | [`hosts/claude-code/`](../hosts/claude-code/README.md) | plugin, from this repository's marketplace | `agent-jsonl` (the default) |
| Codex CLI | [`hosts/codex/`](../hosts/codex/README.md) | skill folder, and a table in `config.toml` | `rollout-jsonl` |

### Claude Code: `hosts/claude-code/`

The folder is a plugin; the repository is its marketplace. The marketplace file must sit at
`.claude-plugin/marketplace.json` in the repository, where `/plugin marketplace add` looks for
it, so it is the one host file left at the root. Its entry installs the plugin folder alone (a
`git-subdir` source) at the release tag, the same tag `.mcp.json` pins the server to, so the
skill and the server it calls always come from one release, even while `main` moves ahead of
it. A relative source would install the plugin from whatever branch the marketplace was added
from. The plugin brings three things:

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

The entry fetches the folder over HTTPS; the host clones the marketplace itself over SSH when it
finds a GitHub key, so set `CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1` if you have none. An install made
before 0.4.1, when the plugin was the repository root, keeps working at its version; to move to
this layout, update the marketplace (`/plugin marketplace update sealed-panel`) once the release
that carries it is tagged, then the plugin (`claude plugin update sealed-panel@sealed-panel` in
a shell).

The plugin works out of the box: its one setting, the transcript folder, defaults to the
`projects` folder of the host's configuration directory under your home, which the server
expands, and reaches the server as `--transcript-root`. If you moved that directory, change the
folder in the plugin's settings (`/config`). Left empty, the server reads no transcript.
Start the host from a project directory: a host started from your home folder would put the
transcripts under the server's root, so the server turns transcripts off and says why in
`status` and in every refusal. The plugin needs `uvx` on the path.

**A judge without tools.** Launch each judge from the top-level session as a fresh `eval-judge`
subagent whose prompt is one brief's text. The agent file sets `tools: []`. The host's
reference does not document an empty list, but measured on its version 2.1.316, it gives zero
tools to a project agent and to a plugin agent, while leaving the field out would grant every
tool. If a later version refuses it, the agent fails to launch, loudly. The file also denies
every built-in tool by name, and MCP tools with `mcp__*`, as a second layer, and the
instructions forbid tools.

That is not quite no tool: in the host's auto mode a handback tool (`SubagentHandback`) is
imposed despite all this, and the judge's answer arrives through it instead of a final text.
That tool only returns the reply, and the `agent-jsonl` adapter reads its message as the reply
(see [`agent-jsonl`](#claude-code-agent-jsonl)). A judge that can read files could open the run
directory and learn which label is the candidate, so a reply whose transcript shows any other
tool call is rejected by `record`, and an agent with any tool call at all, whichever its name,
counts as having replied and cannot be voided. Give the judge one brief's text as its whole
prompt, never a path.

**Working on the plugin from a clone.** Load the folder for one session with `claude
--plugin-dir hosts/claude-code`, or add a local marketplace that lists a copy of it by a
relative path, and point the server at the clone instead of the published package, in your
host's MCP configuration:

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

Symbolic links do not replace the copies: the host reads the plugin folder alone, and a link
from it to `skill/` points outside what it fetches.

### Codex CLI: `hosts/codex/`

The Codex CLI reads skills from `.agents/skills/` in every folder from the one it starts in up
to the repository root, and from `~/.agents/skills/` for the user, and MCP servers from
`config.toml`. The folder holds the skill (`skills/sealed-panel/`, a copy of `skill/`) and the
server table (`config.toml`). Copy the skill into `~/.agents/skills/` (Codex also follows a
link to a skill folder) and the table into `~/.codex/config.toml`, from a clone at the release
tag; [the folder's README](../hosts/codex/README.md) gives the commands. The table:

- starts the server with `uvx`, pinned to the release tag, and `--transcript-root
  ~/.codex/sessions`, the folder where Codex writes its sessions (the server expands `~`);
- raises `startup_timeout_sec` to 120, since the first start fetches the package and Codex
  waits 10 seconds by default;
- raises `tool_timeout_sec` to 300, because a `judge` call runs one judge after another and a
  judge can take a minute, while Codex stops a tool call after 60 seconds by default. Split a
  large panel across calls with the tool's `judges` parameter so that each call fits;
- lists in `env_vars` the variables passed to the server: only what the judge command needs
  (your model's key, if the runner calls a model), none in host mode. The judge command still
  sees only `PATH`, `HOME`, `LANG` and the names in `[judge] env_allow`, so a key reaches a
  judge only when both lists name it.

Since 0.4.1 the repository root no longer holds `.agents/skills/`: Codex started in a clone no
longer finds the skill there, and a copy in your skill folder replaces it.

**A judge without tools.** No way to start a Codex session without tools is known. Start each
judge as a fresh session, from an empty directory, with one brief's text as its whole prompt,
and keep it to one turn; set `format = "rollout-jsonl"` under `[transcript]` in the file given
to `init --config`, and the adapter rejects any tool call the transcript shows (see
[`rollout-jsonl`](#codex-cli-rollout-jsonl)).

## Transcript formats

In host mode, a judge's transcript is the proof that it received its brief and nothing else. A
host adapter reads one transcript format; `[transcript] format`, frozen at `init`, names the one
a run uses ([configuration.md](configuration.md#transcript)). The two formats of this release
were built from the structure of real transcripts on one machine (key names, entry types and
counts, never their text), and are tested on synthetic files of the same structure.

Every adapter refuses the same things, whatever the host:

| What the transcript shows | What `record` does | Why |
|---|---|---|
| A structure the adapter does not recognise | refuses it, stores nothing | a guess at an unknown format could miss a tool call or a later input; check `format`, or upgrade |
| A tool call, other than the format's own handback tool; any item that is neither a message nor reasoning counts as one | records the attempt as rejected | a judge with a tool could have read the run and learned which item is the candidate |
| A session that carries another session's history (a fork) | records the attempt as rejected | the history can hold the run's labels or other judges' replies |
| An input after the judge's first output, beyond what the host adds on its own; a message from another agent always counts | records the attempt as rejected | an input after an answer can steer it or ask again: a re-roll |
| Text before the first output besides the brief, a message from another agent included | records the attempt, and the report flags the characters added | the judge read more than its brief |

A rejected attempt counts against the seat's replacements: brief a fresh judge on the same
brief. Launch judges from an empty directory: a host that injects project instructions reads
them from the directory the agent starts in, and they reach the judge with its brief.

Known limits:

- Project instructions are flagged on one host and not on the other. Where the host marks them
  as its own (an attachment in `agent-jsonl`), they are not counted in the prompt, as in 0.4.0;
  where it does not (user messages in `rollout-jsonl`), they are counted and flagged. So the
  same kind of input shows in the report for one host only. A separate field for the context a
  host adds on its own is planned with the result file of the next release.
- What a host adds as its own, before or after the first output, is not counted: an adapter
  trusts the host's own marks. A host message that steers a judge is not seen.
- The adapter does not read session-state lines; instructions a host injects through them are
  not seen and not counted (see the `rollout-jsonl` section).

Launch every judge from the top-level session itself (the main session, the one you talk
to), each as a fresh subagent whose prompt is one brief. Before a round is aggregated, the
transcripts the adapter lists for the recorded ones (below, for each format) are searched for
the round's brief ids, and every session that holds one and was never recorded blocks the round:

- a brief id in its prompt (its inputs before its first output) makes it a judge: record it (as
  a void when it never replied);
- a brief id anywhere else (a later input, a tool's input or output) blocks the round too, in
  every session but one: the top-level session the recorded judges were launched from, when no
  brief id is in its own prompt. So it may read the brief files and pass each to a judge. An
  intermediate helper agent that handles the briefs, or a standby agent shown a brief, blocks
  the round: record it if it judged, otherwise launch the judges from the top-level session.

Only files changed since the round was briefed (minus 60 seconds) are read, at most 1000; past
that, aggregation is refused rather than stop short. A file is searched for brief ids whatever
its size. One that holds a brief id is then read to tell whose it is: a file that cannot be
read, or one larger than 20 MB, blocks the round. The top-level session's file is the
exception for size: its prompt sits at its start, so only its first 20 MB are read, and if they
show no output yet, the round is blocked, since its prompt cannot be checked. A file changed
before the briefs, or whose date was set back, is not read.

### Claude Code: `agent-jsonl`

The default format. Claude Code writes one JSONL file per subagent, `agent-<id>.jsonl`, under
`~/.claude/projects/<project>/<session>/subagents/`, at its top or in subfolders such as
`workflows/wf_<id>/`, and every entry carries the subagent's `agentId`, the id to give `record
--agent-id`. Give the plugin's server that
`projects` folder as its transcript root (the plugin does by default). The judge is the
plugin's `eval-judge` agent, which has no tools (see
[Claude Code](#claude-code-hostsclaude-code)); its answer is its last text, or the message of
its `SubagentHandback` call in auto mode.

What the adapter reads, from the 413 tool-free subagent transcripts among 3,564 measured,
written by versions 2.1.219 to 2.1.325:

- The host's own messages are user entries marked `isMeta` that carry no `origin`. Before the
  first output it adds one (355 of 413, the same 341 characters each time), with attachments
  (environment, model, date, project instructions). None of these is counted in the prompt.
  Project instructions (your instruction files) arrive as an attachment, so the report does not
  flag them on this host; launching from an empty directory keeps the project's own out.
- Messages from agents carry an `origin`, on the entry or on its attachment: `coordinator` (the
  agent that launched the subagent), `peer` (another agent) or `task-notification`; older host
  versions write queued commands with none (182 `queued_command` attachments, versions 2.1.260
  to 2.1.280), so every queued command is an agent's too. No origin measured names the host, so
  any origin, of a kind known or not, is an agent's: before the first output it is part of the
  prompt, so the whole-brief check and the extra-text flag see a steer sent right after
  launch; after it, it is a later input.
- After the first output, the host adds `prompt_snapshot` attachments and the result of the
  handback call, and may repeat a message of its own (1 of the 413; 9 files hold that same
  message): all allowed. Any other attachment, and any user entry with text that is not the
  host's own, counts as a later input. 1 of the 413 has one (3 messages from the agent that
  launched it, after its answer) and would be refused: the judge is briefed again.
- No fork marker exists in this format: every entry of the transcripts measured is marked as
  the subagent's. A history carried from another session would put that session's prompt and
  calls before the brief, so the prompt check or the tool rule refuses it; a fork that passes
  both is not detected.
- Entry types the host adds between versions (`api-request` and others) are skipped; the
  adapter checks the shape of `user`, `assistant` and `attachment` entries only. All 3,564
  transcripts read without a structure refusal.

The scan for unrecorded judges reads every file under the session's `subagents/` folder, at any
depth, but the host's side files (`*.meta.json`, `*.prefix.json`) and the workflow runtime's log
(`journal.jsonl`, one in each `workflows/wf_<id>/` folder, which stores each agent's result),
and the session's main file,
`~/.claude/projects/<project>/<session>.jsonl`, named by the `sessionId` every entry of a
subagent's transcript carries (measured: one session per transcript, whose file exists, 3,564 of
3,564; the folder above `subagents/` has the same name in 3,563, and both names are tried). That
main file is the top-level session. So a judge run as the main session, with the brief as its
prompt, that then starts the recorded judge as its subagent, blocks the round, and so does any
subagent that handled a brief. Measured on the 1,175 main session files of this machine: none
holds a brief id in its prompt (5 hold one elsewhere: the sessions that ran real rounds); the
11 larger than 20 MB all show an output within their first 20 MB.

### Codex CLI: `rollout-jsonl`

Set `format = "rollout-jsonl"` under `[transcript]` in the file given to `init --config`. The
Codex CLI writes one file per session, `~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`,
filed by the day the session started. Its first line is the session record; the session's `id`
there is the agent id to give `record --agent-id`. Give the MCP server `~/.codex/sessions` as
its transcript root.

Start each judge as a fresh session, in an empty directory, with one brief's text as its whole
prompt, and keep it to one turn: do not reply to it. The adapter does not know of a way to start
a session with no tools; it rejects any call the transcript shows.

What the adapter reads, from 10 sessions measured (6 of them subagent sessions), written by
versions 0.162.0 and 0.163.0 (alpha builds):

- The host injects project instructions as user-role messages, with nothing to tell them from
  the task (3 of the 8 user messages before the first output of the fresh subagent sessions).
  So every user message before the first output is the prompt, and what is not the brief is
  flagged as a prompt extra. Developer-role messages before it are the host's own and are not
  counted.
- A forked subagent's session record carries `forked_from_id` and
  `subagent_history_start_ordinal`, and its parent's session record follows (3 of the 6
  subagent sessions): it is a fork, rejected. A second session record alone also counts.
- Later user messages arrived only at the start of a new turn in subagent sessions. A judge has
  one turn: every input after its first output, and every turn started after it, counts as a
  later input. Of the 3 tool-free sessions measured, 2 had more than one turn and would be
  refused.
- An `agent_message` after the first output (4 of the 6 subagent sessions hold one) is a later
  input, as is any input item that is not the judge's own output.
- Line types the host adds between versions (`turn_context`, `token_usage_record` and others)
  are skipped; the adapter checks the session record and every model item.
- Known limit: the adapter does not read session-state lines (`world_state`), which hold
  instruction text; instructions the host injects through them are not seen and not counted.
  (Their project instructions also appear as a user message of the same file, 10 of 10, which
  is counted.)

The scan for unrecorded judges reads every rollout under the sessions root (`~/.codex/sessions`)
changed since the round was briefed, whatever its date folder or parent session: a judge
started by a helper subagent, or a session started days before it got the brief, is read. The
chain above each recorded judge is read from the session records (its parent session, that
parent's parent, and so on); the one at the top, with no parent, is the top-level
session, exempt when no brief id is in its prompt. Every other session holding a brief id blocks
the round: a helper subagent between the top-level session and the judges, or another top-level
session that pasted a brief. Start the judges as subagents of the top-level session, or give
`codex exec` the brief file rather than pasting it.

Not verified with a real judge session on this host: which options start a session without
tools, whether a subagent can be started in another directory, and how a subagent hands its
answer back when that is not its last message. The adapter rejects what it cannot place.

## API pipelines

In a pipeline, nothing hosts an agent: the judge runner calls your model. Write a judge command
that reads the brief on stdin and prints the model's answer on stdout, set it in `[judge]
command`, and name the variables it needs (your API key) in `[judge] env_allow`. Start from
`examples/runner/judge_template.py`, which reaches a model through a model command line or an
HTTP endpoint, both set by environment variables; adapt the one function that speaks to your
model's API. `examples/runner/judge.toml` is the matching configuration. Declare the model in
`[judge] model_id`, printed next to the grade: a dated model id, never an alias, which may point
to another model the next day.

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
refusal raises `EvalError` with its reason. [api.md](api.md) lists the stable names, a spec
written in Python (`Spec`) and the exceptions. An HTTP transport for the MCP server is not part
of this release.
