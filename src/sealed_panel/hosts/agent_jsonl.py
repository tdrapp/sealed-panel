"""The `agent-jsonl` format: the transcripts Claude Code writes for its subagents.

One JSON object per line. An entry's `type` is `user`, `assistant`, `attachment` or one of the
host's metadata types (`api-request`, `system` and others, which change between host versions
and are skipped). A user or assistant entry holds `message.content`, a string or a list of blocks,
each with a `type`. Claude Code writes one file per subagent, `agent-<id>.jsonl`, in a
`subagents/` folder beside its session, and every entry carries the subagent's `agentId`. A
subagent's answer is its last text, or the message of a call to the handback tool
(`SubagentHandback`) when the host imposes one.

What the host adds on its own, measured on 413 tool-free subagent transcripts (structure and
counts only): before the first output, a `user` entry marked `isMeta` (355 of 413, always the same
341 characters) and attachments (environment, model, date, project instructions and others);
after the first output, `prompt_snapshot` attachments (380), the tool results of the handback
call (355), and in 4 transcripts a host message marked `isMeta`. A user entry with text (marked
or not), or any other attachment, after the first output is an input the judge got after it
answered, which `record` refuses: the 4 cost a fresh judge each. Project instructions arrive
as an `instructions` attachment, not as text in the prompt, and are not counted in it.

No fork marker was found: every entry of 3,563 subagent transcripts is marked `isSidechain`, and
none carries another session's id. A history carried from another session would show as tool
calls or as a prompt without the brief, both refused.
"""

from __future__ import annotations

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


def _is_input(e: dict[str, Any]) -> bool:
    """An entry that reaches the model as input: a user entry with anything but tool results,
    or an attachment of a type the host does not add on its own after an output."""
    if e["type"] == "user":
        content = _content(e)
        return isinstance(content, str) or any(b["type"] != "tool_result" for b in content)
    return e["type"] == "attachment" and e["attachment"]["type"] not in LATER_HOST_ATTACHMENTS


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
        prompt="\n".join(
            _text(_content(e)) for e in _of(entries[:first], "user") if not e.get("isMeta")
        ),
        reply=reply,
        reply_problem=problem,
        tools=tuple(sorted({name for kind, name in calls if (kind, name) != exempt})),
        # Fails closed: any call counts as a reply, whichever its name, because a runtime may
        # deliver the answer through a tool no one knows of. Only silence can be voided.
        replied=bool(calls) or bool(_texts(entries)),
        later_inputs=sum(_is_input(e) for e in entries[first + 1 :]),
        unreadable=lines.unreadable,
    )
