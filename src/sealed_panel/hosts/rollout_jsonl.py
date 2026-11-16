"""The `rollout-jsonl` format: the session files the Codex CLI writes, one per session.

One JSON object per line: `{"timestamp", "type", "payload"}`. The first line is `session_meta`,
whose payload holds the session's `id` (the agent id), its `cwd` and, for a subagent, the
`parent_thread_id`. A `response_item` payload is one model item: a `message` (role `user`,
`developer` or `assistant`, content a list of `input_text` or `output_text` blocks), `reasoning`,
an `agent_message` between agents, `additional_tools` (the tools the host declares), or a call
and its output (`function_call`, `custom_tool_call`, `*_output` and others). An `event_msg` with
payload type `task_started` opens a turn. Other line types (`turn_context`, `token_usage_record`
and more) change between host versions and are skipped. The files are filed by the day the
session started: `sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl`.

Measured on 10 rollouts (structure and counts only):

- A forked subagent carries its parent's history: its `session_meta` holds `forked_from_id` and
  `subagent_history_start_ordinal`, and the parent's `session_meta` follows (3 of 6 subagent
  sessions). Such a session is a fork.
- The host injects project instructions as user-role messages, with nothing to tell them from the
  task: 3 of the 8 user messages before the first output in fresh subagent sessions open with the
  project instructions header. So every user message before the first output is the prompt, and
  what is not the brief is a prompt extra, flagged. Developer messages before it are the host's
  own (2 per fresh subagent session) and are not counted.
- In the 3 fresh subagent sessions, the 10 user messages after the first output each open a new
  turn; a user message after an output within one turn was seen only in top-level sessions. A
  judge has one turn: every input after its first output, and every turn started after it, is
  counted as a later input. Of the 3 tool-free sessions, 1 has a single turn; the 2 others would
  be refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import FormatError
from ..transcript import Transcript, read_lines

# Model items that are input to the agent; an item of any other type is a call.
INPUTS = frozenset({"message", "agent_message", "additional_tools"})
REASONING = "reasoning"
FORK_KEYS = ("forked_from_id", "subagent_history_start_ordinal")


def _payload(e: dict[str, Any]) -> dict[str, Any]:
    p = e.get("payload")
    return p if isinstance(p, dict) else {}


def _text(item: dict[str, Any]) -> str:
    return "".join(
        str(b.get("text", ""))
        for b in item["content"]
        if b["type"] in ("input_text", "output_text", "text")
    )


def _item_ok(item: dict[str, Any]) -> bool:
    """A model item has a type; a message also a role and a list of typed blocks."""
    if not isinstance(item.get("type"), str):
        return False
    if item["type"] not in ("message", "agent_message"):
        return True
    content = item.get("content")
    return (item["type"] != "message" or isinstance(item.get("role"), str)) and (
        isinstance(content, list)
        and all(isinstance(b, dict) and isinstance(b.get("type"), str) for b in content)
    )


def _session(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any]:
    """The first line's session record, refused when it is not one."""
    first = entries[0] if entries else {}
    meta = _payload(first)
    if first.get("type") != "session_meta" or not isinstance(meta.get("id"), str):
        raise FormatError(
            f"{path} does not open with a session record: not a rollout-jsonl transcript; "
            "check [transcript] format"
        )
    return meta


def _items(path: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model items and turn starts, in order; a turn start is `{"type": "task_started"}`."""
    out = []
    for e in entries:
        if e.get("type") == "response_item":
            item = _payload(e)
            if not _item_ok(item):
                raise FormatError(
                    f"{path} is not a rollout-jsonl transcript this version reads (a model item "
                    "has another shape): check [transcript] format, or upgrade sealed-panel"
                )
            out.append(item)
        elif e.get("type") == "event_msg" and _payload(e).get("type") == "task_started":
            out.append({"type": "task_started"})
    return out


def _is_output(item: dict[str, Any], me: str | None) -> bool:
    """An item the agent produced: an assistant message, a message it sent to another agent, or
    a call."""
    if item["type"] == "message":
        return bool(item["role"] == "assistant")
    if item["type"] == "agent_message":
        return me is not None and item.get("author") == me
    return item["type"] not in INPUTS | {REASONING, "task_started"}


def _own_path(meta: dict[str, Any]) -> str | None:
    """The session's own agent path, when the host records it."""
    path = meta.get("agent_path")
    return path if isinstance(path, str) else None


def _prompt(items: list[dict[str, Any]]) -> str:
    """Every user message and every message from another agent before the first output."""
    return "\n".join(
        _text(i)
        for i in items
        if (i["type"] == "message" and i["role"] == "user") or i["type"] == "agent_message"
    )


def _later(items: list[dict[str, Any]], me: str | None) -> int:
    """Inputs after the first output, of any role, and turns started after it."""
    return sum(
        i["type"] == "task_started" or (i["type"] in INPUTS and not _is_output(i, me))
        for i in items
    )


def _calls(items: list[dict[str, Any]], me: str | None) -> list[str]:
    """Every call, by its name when it has one: every output but an assistant message."""
    return [
        str(i.get("name") or i["type"])
        for i in items
        if _is_output(i, me) and i["type"] != "message"
    ]


def _first_output(items: list[dict[str, Any]], me: str | None) -> int:
    """The index of the first item the agent produced."""
    return next((n for n, i in enumerate(items) if _is_output(i, me)), len(items))


def _texts(items: list[dict[str, Any]]) -> list[str]:
    """Every non-empty assistant message text, in order: the last one is the reply."""
    texts = [_text(i) for i in items if i["type"] == "message" and i["role"] == "assistant"]
    return [t for t in texts if t.strip()]


def _forked(meta: dict[str, Any], entries: list[dict[str, Any]]) -> bool:
    """A session that carries another one's history: marked as a fork, or holding a second
    session record."""
    sessions = sum(e.get("type") == "session_meta" for e in entries)
    return any(k in meta for k in FORK_KEYS) or sessions > 1


def read(path: Path) -> Transcript:
    """Read one rollout; one this adapter does not recognise raises FormatError.

    Args:
        path: The rollout file.
    """
    lines = read_lines(path)
    meta = _session(path, lines.entries)
    items = _items(path, lines.entries)
    me = _own_path(meta)
    first = _first_output(items, me)
    texts = _texts(items)
    calls = _calls(items, me)
    return Transcript(
        path=path,
        agent_id=meta["id"],
        prompt=_prompt(items[:first]),
        reply=None if lines.cut_off else (texts[-1] if texts else None),
        reply_problem=lines.cut_off,
        tools=tuple(sorted(set(calls))),
        replied=bool(calls) or bool(texts),
        later_inputs=_later(items[first + 1 :], me),
        fork=_forked(meta, lines.entries),
        unreadable=lines.unreadable,
    )
