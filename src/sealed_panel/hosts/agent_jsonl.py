"""The `agent-jsonl` format: the transcripts Claude Code writes for its subagents.

One JSON object per line. An entry's `type` is `user`, `assistant`, `attachment` or one of the
host's metadata types; a user or assistant entry holds `message.content`, a string or a list of
blocks, each with a `type`. Claude Code writes one file per subagent, `agent-<id>.jsonl`, in a
`subagents/` folder beside its session, and every entry carries the subagent's `agentId`. A
subagent's answer is its last text, or the message of a call to the handback tool
(`SubagentHandback`) when the host imposes one.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..store import read_text
from ..transcript import Transcript, read_lines

# The tool some hosts make a subagent call to return its answer. Its call is the reply, not a
# tool use; any other call is a tool use.
HANDBACK_TOOL = "SubagentHandback"
TOOL_BLOCK = "tool_use"
# Characters searched for the agent id, from the start of a transcript.
AGENT_ID_SCAN_CHARS = 20_000
AGENT_ID = re.compile(r'"agentId"\s*:\s*"([^"]+)"')


def _content(e: dict[str, Any]) -> Any:
    message = e.get("message")
    return message.get("content") if isinstance(message, dict) else None


def _blocks(e: dict[str, Any]) -> list[dict[str, Any]]:
    content = _content(e)
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _of(entries: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    return [e for e in entries if e.get("type") == role]


def _refused_ids(entries: list[dict[str, Any]]) -> set[Any]:
    """The ids of the calls whose tool result came back as an error: the runtime refused them,
    so they never reached the caller."""
    return {
        b.get("tool_use_id")
        for e in _of(entries, "user")
        for b in _blocks(e)
        if b.get("type") == "tool_result" and b.get("is_error") is True
    }


def _handback_calls(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every call to the handback tool, in order."""
    return [
        b
        for e in _of(entries, "assistant")
        for b in _blocks(e)
        if b.get("type") == TOOL_BLOCK and b.get("name") == HANDBACK_TOOL
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


def _texts(entries: list[dict[str, Any]]) -> list[str]:
    """Every non-empty assistant text, in order."""
    texts = [_text(_content(e)) for e in _of(entries, "assistant") if isinstance(_content(e), list)]
    return [t for t in texts if t.strip()]


def _calls(entries: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(block type, name) of every tool call. Any type ending like the tool block: a server's
    tool call is a tool call too."""
    return [
        (str(b.get("type")), str(b.get("name")))
        for e in _of(entries, "assistant")
        for b in _blocks(e)
        if str(b.get("type")).endswith(TOOL_BLOCK)
    ]


def read(path: Path) -> Transcript:
    """Read one subagent transcript.

    Args:
        path: The transcript file.
    """
    lines = read_lines(path)
    entries = lines.entries
    users = _of(entries, "user")
    reply, problem = _reply(path, lines.cut_off, entries)
    calls = _calls(entries)
    exempt = (TOOL_BLOCK, HANDBACK_TOOL)
    found = AGENT_ID.search(read_text(path)[:AGENT_ID_SCAN_CHARS])
    return Transcript(
        path=path,
        agent_id=found.group(1) if found else None,
        prompt=_text(_content(users[0])) if users else "",
        reply=reply,
        reply_problem=problem,
        tools=tuple(sorted({name for kind, name in calls if (kind, name) != exempt})),
        # Fails closed: any tool call counts as a reply, whichever its name, because a runtime
        # may deliver the answer through a tool no one knows of. Only silence can be voided.
        replied=bool(calls) or bool(_texts(entries)),
        unreadable=lines.unreadable,
    )
