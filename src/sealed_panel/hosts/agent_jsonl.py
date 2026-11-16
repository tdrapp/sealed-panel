"""The `agent-jsonl` format: the transcripts Claude Code writes for its subagents.

One JSON object per line. An entry's `type` is `user`, `assistant`, `attachment` or one of the
host's metadata types (`api-request`, `system` and others, which change between host versions
and are skipped). A user or assistant entry holds `message.content`, a string or a list of blocks,
each with a `type`. Claude Code writes one file per subagent, `agent-<id>.jsonl`, under the
`subagents/` folder beside its session, at its top or in subfolders such as `workflows/wf_<id>/`
(389 of 3,564 files measured), and every entry carries the subagent's `agentId`. A subagent's
answer is its last text, or the message of a call to the handback tool (`SubagentHandback`)
when the host imposes one.

Who wrote an entry, measured on 3,564 transcripts (413 tool-free; structure and counts only):

- The host's own messages are user entries marked `isMeta` with no `origin`: before the first
  output in 355 of the 413 (always the same 341 characters), and after it in tool-using
  transcripts and in 1 tool-free one (a message the host repeats word for word; 9 files hold
  it). They are not inputs: not part of the prompt, not a later input.
- Messages from agents carry an `origin`, on the entry or on its attachment, whose `kind` is
  `coordinator` (the agent that launched the subagent: 385 user entries, 54 attachments),
  `peer` (7 and 54) or `task-notification` (65 and 383). No origin names the host, so any origin,
  of a kind known or not, is an agent's: before the first output it is part of the prompt (the
  whole-brief check and the extra-text count see it), after it a later input. Older host
  versions (2.1.260 to 2.1.280) write queued commands with no origin (182 `queued_command`
  attachments), so every queued command is an agent's too.
- Attachments before the first output (environment, model, date, project instructions in 385 of
  the 413, and others) are the host's and are not counted. After it, the host adds only
  `prompt_snapshot` attachments (380) and the tool results of the handback call (355); a user
  entry with text, or any other attachment, is a later input, which `record` refuses. Of the 413,
  1 is refused this way: 3 messages from the agent that launched it, after its answer.

No fork marker was found: every entry is marked `isSidechain`, and none carries another
session's id. A history carried from another session would show as tool calls or as a prompt
without the brief, both refused; a fork that passes both is not detected.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..errors import FormatError
from ..transcript import Transcript, read_lines

# The tool some hosts make a subagent call to return its answer. Its call is the reply, not a
# tool use; any other call is a tool use.
HANDBACK_TOOL = "SubagentHandback"
TOOL_BLOCK = "tool_use"
# Assistant blocks that are reasoning, not output.
REASONING = frozenset({"thinking", "redacted_thinking"})
# Every other assistant block but text is a call: fails closed on a block type no one knows of.
NOT_CALLS = REASONING | {"text"}
# The only attachment the host adds after a judge's first output.
LATER_HOST_ATTACHMENTS = frozenset({"prompt_snapshot"})
# The entry types whose shape this adapter checks; any other type is host metadata, skipped.
MESSAGES = ("user", "assistant")


def _content(e: dict[str, Any]) -> Any:
    message = e.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(e: dict[str, Any]) -> list[dict[str, Any]]:
    content = _content(e)
    return content if isinstance(content, list) else []


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(b.get("text", "")) for b in content if b["type"] == "text")


def _of(entries: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [e for e in entries if e["type"] == role]


def _content_ok(kind: str, content: Any) -> bool:
    """A user entry's content is a string or a list of typed blocks; an assistant entry's is a
    list of typed blocks."""
    if isinstance(content, str):
        return kind == "user"
    return isinstance(content, list) and all(
        isinstance(b, dict) and isinstance(b.get("type"), str) for b in content
    )


def _shape_problem(e: dict[str, Any]) -> str:
    """Why an entry does not have the shape of its type, or empty."""
    kind = e.get("type")
    if not isinstance(kind, str):
        return "an entry has no type"
    if kind == "attachment":
        a = e.get("attachment")
        return "" if isinstance(a, dict) and isinstance(a.get("type"), str) else "an attachment"
    if kind in MESSAGES and not _content_ok(kind, _content(e)):
        return f"a {kind} entry"
    return ""


def _check(path: Path, entries: list[dict[str, Any]]) -> None:
    """Refuse a file this adapter does not recognise: never a guess at what it holds."""
    for e in entries:
        problem = _shape_problem(e)
        if problem:
            raise FormatError(
                f"{path} is not an agent-jsonl transcript this version reads ({problem} has "
                "another shape): check [transcript] format, or upgrade sealed-panel"
            )
    if not any(e["type"] in MESSAGES for e in entries):
        raise FormatError(
            f"{path} holds no user or assistant entry: not an agent-jsonl transcript; check "
            "[transcript] format"
        )


def _agent_id(path: Path, entries: list[dict[str, Any]]) -> str | None:
    ids = {e["agentId"] for e in entries if isinstance(e.get("agentId"), str)}
    if len(ids) > 1:
        raise FormatError(f"{path} carries {len(ids)} agent ids: one transcript is one agent")
    return next(iter(ids), None)


def _first_output(entries: list[dict[str, Any]]) -> int:
    """The index of the first assistant entry with a block that is not reasoning."""
    for i, e in enumerate(entries):
        if e["type"] == "assistant" and any(b["type"] not in REASONING for b in _blocks(e)):
            return i
    return len(entries)


def _agent_origin(e: dict[str, Any]) -> bool:
    """Whether an entry comes from an agent rather than from the host: it carries an `origin`,
    on itself or on its attachment, or it is a queued command. Every origin measured names an
    agent or a task (the agent that launched the judge, another agent, a task notification);
    none names the host, so any origin, of a kind known or not, is an agent's. Queued commands
    from older host versions carry no origin (182 measured), so one is an agent's whatever it
    carries."""
    attachment = e.get("attachment")
    inner = attachment.get("origin") if isinstance(attachment, dict) else None
    queued = isinstance(attachment, dict) and attachment.get("type") == "queued_command"
    return e.get("origin") is not None or inner is not None or queued


def _host_own(e: dict[str, Any]) -> bool:
    """A message the host adds on its own: a user entry marked `isMeta`, with no origin."""
    return e["type"] == "user" and e.get("isMeta") is True and not _agent_origin(e)


def _is_input(e: dict[str, Any]) -> bool:
    """An entry that reaches the model as input after an output: anything from an agent; a user
    entry with anything but tool results, unless it is the host's own message; an attachment of
    a type the host does not add on its own after an output."""
    if _agent_origin(e):
        return True
    if e["type"] == "user":
        content = _content(e)
        if _host_own(e):
            return False
        return isinstance(content, str) or any(b["type"] != "tool_result" for b in content)
    return e["type"] == "attachment" and e["attachment"]["type"] not in LATER_HOST_ATTACHMENTS


def _prompt(entries: list[dict[str, Any]]) -> str:
    """The inputs before the first output: every user entry but the host's own messages, and
    every attachment from an agent (its prompt text, else the whole attachment)."""
    texts = []
    for e in entries:
        if e["type"] == "user" and not _host_own(e):
            texts.append(_text(_content(e)))
        elif e["type"] == "attachment" and _agent_origin(e):
            prompt = e["attachment"].get("prompt")
            texts.append(prompt if isinstance(prompt, str) else json.dumps(e["attachment"]))
    return "\n".join(texts)


def _refused_ids(entries: list[dict[str, Any]]) -> set[Any]:
    """The ids of the calls whose tool result came back as an error: the runtime refused them,
    so they never reached the caller."""
    return {
        b.get("tool_use_id")
        for e in _of(entries, "user")
        for b in _blocks(e)
        if b["type"] == "tool_result" and b.get("is_error") is True
    }


def _handback_calls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every call to the handback tool, in order."""
    return [
        b
        for e in _of(entries, "assistant")
        for b in _blocks(e)
        if b["type"] == TOOL_BLOCK and b.get("name") == HANDBACK_TOOL
    ]


def _handbacks(entries: list[dict[str, Any]], include_refused: bool) -> list[str | None]:
    """The messages of the handback calls, in order; None for a call whose message is not one
    non-empty string (never coerced into a reply). A refused call is left out unless asked for."""
    refused = _refused_ids(entries)
    messages: list[str | None] = []
    for b in _handback_calls(entries):
        call_id = b.get("id")
        if not include_refused and call_id is not None and call_id in refused:
            continue  # refused by the runtime: never delivered
        inp = b.get("input")
        msg = inp.get("message") if isinstance(inp, dict) else None
        messages.append(msg if isinstance(msg, str) and msg.strip() else None)
    return messages


def _texts(entries: list[dict[str, Any]]) -> list[str]:
    """Every non-empty assistant text, in order."""
    texts = [_text(_blocks(e)) for e in _of(entries, "assistant")]
    return [t for t in texts if t.strip()]


def _reply(path: Path, cut_off: str, entries: list[dict[str, Any]]) -> tuple[str | None, str]:
    """The reply and why it cannot be read. The last delivered handback message, since that is
    what the caller received; else the last non-empty text; else, when nothing was delivered at
    all, the last refused handback, which the judge still wrote."""
    if cut_off:
        return None, cut_off
    delivered = _handbacks(entries, include_refused=False)
    if delivered:
        last = delivered[-1]
        if last is None:
            return None, f"malformed handback in {path}: its message is not a text"
        return last, ""
    texts = _texts(entries)
    if texts:
        return texts[-1], ""
    refused = _handbacks(entries, include_refused=True)
    if refused:
        if refused[-1] is None:
            return None, f"malformed refused handback in {path}: its message is not a text"
        return refused[-1], ""
    return None, ""


def _calls(entries: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(block type, name) of every call: every assistant block that is neither text nor
    reasoning, named by its tool when it has one."""
    return [
        (b["type"], str(b.get("name") or b["type"]))
        for e in _of(entries, "assistant")
        for b in _blocks(e)
        if b["type"] not in NOT_CALLS
    ]


def read(path: Path) -> Transcript:
    """Read one subagent transcript; one this adapter does not recognise raises FormatError.

    Args:
        path: The transcript file.
    """
    lines = read_lines(path)
    entries = lines.entries
    _check(path, entries)
    first = _first_output(entries)
    calls = _calls(entries)
    exempt = (TOOL_BLOCK, HANDBACK_TOOL)
    reply, problem = _reply(path, lines.cut_off, entries)
    return Transcript(
        path=path,
        agent_id=_agent_id(path, entries),
        prompt=_prompt(entries[:first]),
        reply=reply,
        reply_problem=problem,
        tools=tuple(sorted({name for kind, name in calls if (kind, name) != exempt})),
        # Fails closed: any call counts as a reply, whichever its name, because a runtime may
        # deliver the answer through a tool no one knows of. Only silence can be voided.
        replied=bool(calls) or bool(_texts(entries)),
        later_inputs=sum(_is_input(e) for e in entries[first + 1 :]),
        unreadable=lines.unreadable,
    )


# Files the host writes beside a subagent's transcript that are not transcripts.
SIDE_FILES = (".meta.json", ".prefix.json")
# A session or agent id read from a file, before it names another file.
PLAIN_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _tree(path: Path) -> Path:
    """The session's `subagents/` folder above a transcript (the host also files transcripts in
    subfolders of it, such as `workflows/wf_<id>/`); the transcript's own folder when it lies
    under none."""
    return next((p for p in path.parents if p.name == "subagents"), path.parent)


def _plain(name: Any) -> bool:
    """A value fit to name a file: letters, digits, `_` and `-` only."""
    return isinstance(name, str) and PLAIN_NAME.fullmatch(name) is not None


def _sessions(path: Path) -> set[Path]:
    """The main session file a subagent transcript belongs to, `<project>/<session>.jsonl`
    beside the session's folder. Measured: every subagent transcript names one session in its
    entries' `sessionId`, whose file exists (3,564 of 3,564); the folder above `subagents/` has
    the same name in 3,563. Both names are tried; none for a transcript under no `subagents/`."""
    tree = next((p for p in path.parents if p.name == "subagents"), None)
    if tree is None:
        return set()
    ids = {tree.parent.name} | {e.get("sessionId") for e in read_lines(path).entries}
    return {tree.parent.parent / f"{i}.jsonl" for i in ids if _plain(i)}


def _parents(path: Path) -> set[Path]:
    """The subagents that launched this one, at any depth, from the `parentAgentId` of each
    one's meta file (measured: 281 of 281 name a transcript of the same tree)."""
    tree, current = _tree(path), path
    found: set[Path] = set()
    while True:
        try:
            meta = json.loads(current.with_name(current.stem + ".meta.json").read_text("utf-8"))
        except (OSError, ValueError, RecursionError):
            return found
        parent = meta.get("parentAgentId") if isinstance(meta, dict) else None
        nxt = next(tree.rglob(f"agent-{parent}.jsonl"), None) if _plain(parent) else None
        if nxt is None or nxt in found:
            return found
        found.add(nxt)
        current = nxt


def siblings(paths: list[Path], since: float) -> list[Path]:
    """Every file under the `subagents/` folder of each recorded transcript's session, at any
    depth, the host's side files left out, and the main session file each belongs to, last
    changed at or after `since`. A purged folder lists nothing.

    Args:
        paths: The recorded transcripts.
        since: The earliest last-change time, in seconds since the epoch.
    """
    tree = {f for root in {_tree(p) for p in paths} for f in root.rglob("*")}
    mains = {f for p in paths for f in _sessions(p)}
    return sorted(
        f
        for f in tree | mains
        if f.is_file() and not f.name.endswith(SIDE_FILES) and f.stat().st_mtime >= since
    )


def launchers(paths: list[Path], candidates: list[Path]) -> set[Path]:
    """The files of the sessions that launched the recorded judges: each one's main session and
    the subagents above it.

    Args:
        paths: The recorded transcripts.
        candidates: The files the scan reads (unused: the launchers are found from the
            recorded transcripts alone).
    """
    return {f for p in paths for f in _sessions(p) | _parents(p)}
