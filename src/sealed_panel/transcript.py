"""Read a judge's transcript: a JSONL file whose assistant entries carry text blocks.

The field names of a transcript entry are set in the [transcript] section of the
configuration. This module only reads: what a transcript proves is decided by `record`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import Config, resolve
from .errors import EvalError, MalformedReply
from .store import read_text

# Characters searched for the agent id, from the start of a transcript.
AGENT_ID_SCAN_CHARS = 20_000


def transcript_agent_id(transcript: Path, cfg: Config | None = None) -> str | None:
    """The agent id the transcript itself carries (None when it carries none)."""
    t = resolve(cfg).transcript
    pattern = '"' + re.escape(str(t["agent_id_key"])) + r'"\s*:\s*"([^"]+)"'
    m = re.search(pattern, read_text(transcript)[:AGENT_ID_SCAN_CHARS])
    return m.group(1) if m else None


def _lines(transcript: Path) -> list[tuple[int, dict[str, Any] | None]]:
    """(line number, entry) for every non-empty line; None for a line that is not a JSON object.
    Lines end at a newline only: a JSON string may hold other line separators."""
    out: list[tuple[int, dict[str, Any] | None]] = []
    for n, line in enumerate(read_text(transcript).split("\n"), start=1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except (ValueError, RecursionError):
            e = None
        out.append((n, e if isinstance(e, dict) else None))
    return out


def _entries(transcript: Path) -> list[dict[str, Any]]:
    return [e for _, e in _lines(transcript) if e is not None]


def unreadable_lines(transcript: Path) -> list[int]:
    """The numbers of the transcript's non-empty lines that are not a JSON object. Reading
    skips them; `record` counts them in its result."""
    return [n for n, e in _lines(transcript) if e is None]


def _block_text(content: Any, t: dict[str, Any]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get(t["text_key"], ""))
            for b in content
            if isinstance(b, dict) and b.get(t["block_type_key"]) == t["text_block_type"]
        )
    return ""


def _content(e: dict[str, Any], t: dict[str, Any]) -> Any:
    message = e.get(t["message_key"])
    return message.get(t["content_key"]) if isinstance(message, dict) else None


def transcript_prompt(transcript: Path, cfg: Config | None = None) -> str:
    """The text of the first user entry: the prompt the agent was launched with."""
    t = resolve(cfg).transcript
    for e in _entries(transcript):
        if e.get(t["role_key"]) == t["user_role"]:
            return _block_text(_content(e, t), t)
    return ""


def assistant_texts(transcript: Path, cfg: Config | None = None) -> list[str]:
    """Every non-empty assistant text of a transcript, in order."""
    t = resolve(cfg).transcript
    texts = []
    for e in _entries(transcript):
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        if isinstance(content, list):
            text = _block_text(content, t)
            if text.strip():
                texts.append(text)
    return texts


def handback_messages(
    transcript: Path, cfg: Config | None = None, include_refused: bool = False
) -> list[str | None]:
    """The messages the agent returned through the configured handback tool, in order; None
    for a call whose message is not one non-empty string (never coerced into a reply). Some
    agent runtimes return a subagent's answer as a tool call rather than as a final text.

    A call whose tool result came back as an error was refused by the runtime and never reached
    the caller. By default such calls are left out, so the list holds what was delivered. With
    include_refused, they are kept: last_reply uses that list when nothing was delivered at all,
    and a void uses it because a refused verdict still shows the judge replied. An empty
    handback_tool setting means no tool is read."""
    t = resolve(cfg).transcript
    tool = t["handback_tool"]
    if not tool:
        return []
    entries = _entries(transcript)
    refused = set()
    for e in entries:
        if e.get(t["role_key"]) != t["user_role"]:
            continue
        content = _content(e, t)
        if isinstance(content, list):
            for b in content:
                if (
                    isinstance(b, dict)
                    and b.get(t["block_type_key"]) == t["tool_result_type"]
                    and b.get(t["tool_error_key"]) is True
                ):
                    refused.add(b.get(t["tool_result_id_key"]))
    messages: list[str | None] = []
    for e in entries:
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        if not isinstance(content, list):
            continue
        for b in content:
            if (
                not isinstance(b, dict)
                or b.get(t["block_type_key"]) != t["tool_block_type"]
                or b.get(t["tool_name_key"]) != tool
            ):
                continue
            call_id = b.get(t["tool_id_key"])
            if not include_refused and call_id is not None and call_id in refused:
                continue  # refused by the runtime: never delivered
            inp = b.get(t["tool_input_key"])
            msg = inp.get(t["tool_message_key"]) if isinstance(inp, dict) else None
            messages.append(msg if isinstance(msg, str) and msg.strip() else None)
    return messages


def tool_calls(transcript: Path, cfg: Config | None = None) -> list[tuple[str, str]]:
    """(block type, name) of every tool the agent called in its transcript, in order."""
    t = resolve(cfg).transcript
    names = []
    for e in _entries(transcript):
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        for b in content if isinstance(content, list) else []:
            # Any type ending like the tool block: a server's tool call is a tool call too.
            if isinstance(b, dict) and str(b.get(t["block_type_key"])).endswith(
                str(t["tool_block_type"])
            ):
                names.append((str(b.get(t["block_type_key"])), str(b.get(t["tool_name_key"]))))
    return names


def judge_tools(transcript: Path, cfg: Config | None = None) -> list[str]:
    """The tools the judge called, sorted, the configured handback tool excepted (the way some
    runtimes return a reply). A judge gets no tools: a call to any other tool means it could
    have read the run directory, labels and roles included."""
    t = resolve(cfg).transcript
    exempt = (str(t["tool_block_type"]), str(t["handback_tool"]))
    return sorted({name for kind, name in tool_calls(transcript, cfg) if (kind, name) != exempt})


def last_reply(transcript: Path, cfg: Config | None = None) -> str:
    """A transcript's reply: its last delivered handback message, since that is what its
    caller received; else its last non-empty assistant text; else, when nothing was delivered
    at all, its last refused handback, which the judge still wrote. A malformed last handback,
    delivered or refused, raises MalformedReply; no reply at all is an error, never a guess.

    A last line that is not a JSON object raises MalformedReply: a transcript cut off while
    being written may have lost the final reply, and the text before it is not that reply.

    Args:
        transcript: The judge's JSONL transcript.
        cfg: The run's configuration.
    """
    lines = _lines(transcript)
    if lines and lines[-1][1] is None:
        raise MalformedReply(
            f"the last line of {transcript} (line {lines[-1][0]}) is not a readable entry: the "
            "transcript may have been cut off before its final reply"
        )
    handed = handback_messages(transcript, cfg)
    if handed:
        last = handed[-1]
        if last is None:
            raise MalformedReply(f"malformed handback in {transcript}: its message is not a text")
        return last
    texts = assistant_texts(transcript, cfg)
    if texts:
        return texts[-1]
    refused = handback_messages(transcript, cfg, include_refused=True)
    if refused:
        if refused[-1] is None:
            raise MalformedReply(
                f"malformed refused handback in {transcript}: its message is not a text"
            )
        return refused[-1]
    raise EvalError(f"no assistant reply found in {transcript}")
