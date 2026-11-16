"""A judge's transcript as the core reads it: one neutral record, whatever host wrote the file.

A host adapter (`sealed_panel.hosts`, one per transcript format) turns one transcript file into a
`Transcript`. This module holds that record, the adapter's shape, the JSONL line reader the
adapters share, and the reply rule. It only reads: what a transcript proves is decided by
`record`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import EvalError, MalformedReply
from .store import read_text


@dataclass(frozen=True, slots=True)
class Transcript:
    """What one transcript shows of the agent that wrote it.

    Attributes:
        path: The transcript file.
        agent_id: The agent id the transcript carries, or None.
        prompt: The input that must hold the whole brief.
        reply: The agent's reply, or None when it gave none or gave it in a shape that cannot be
            read.
        reply_problem: Why the reply cannot be read, or empty.
        tools: The tools the agent called, sorted, the format's handback tool excepted.
        replied: Whether the agent produced any output at all (a text or any call).
        later_inputs: How many inputs reached the agent after its first output, beyond what its
            host adds on its own: a later input can steer or re-roll a reply.
        fork: Whether the session carries the history of another session.
        unreadable: The numbers of the non-empty lines that are not a JSON object.
    """

    path: Path
    agent_id: str | None
    prompt: str
    reply: str | None = None
    reply_problem: str = ""
    tools: tuple[str, ...] = ()
    replied: bool = False
    later_inputs: int = 0
    fork: bool = False
    unreadable: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Adapter:
    """One transcript format: how to read a file of it, and where its siblings are.

    Attributes:
        read: Turns one transcript file into a Transcript; with a limit, only its first
            bytes up to the last whole line before it.
        siblings: The transcripts the host wrote beside the recorded ones (these included,
            and the top-level session) and last changed at or after a time, for the scan for
            unrecorded judges.
        roots: Among those, the top-level session the recorded judges were launched from.
    """

    read: Callable[[Path, int | None], Transcript]
    siblings: Callable[[list[Path], float], list[Path]]
    roots: Callable[[list[Path], list[Path]], set[Path]]


@dataclass(frozen=True, slots=True)
class Lines:
    """A JSONL file, read line by line.

    Attributes:
        entries: Every line that is a JSON object, in order.
        unreadable: The numbers of the non-empty lines that are not a JSON object.
        cut_off: Why the last line makes the reply untrustworthy, or empty.
    """

    entries: list[dict[str, Any]]
    unreadable: tuple[int, ...]
    cut_off: str


def _entry(line: str) -> dict[str, Any] | None:
    """A line's JSON object, or None for a line that is not one."""
    try:
        e = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return e if isinstance(e, dict) else None


def _head(path: Path, limit: int) -> str:
    """The file's first `limit` bytes, up to the last whole line among them."""
    with path.open("rb") as fh:
        data = fh.read(limit + 1)
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace")
    return data[:limit].rsplit(b"\n", 1)[0].decode("utf-8", errors="replace")


def read_lines(path: Path, limit: int | None = None) -> Lines:
    """Read a JSONL transcript. Lines end at a newline only: a JSON string may hold other line
    separators. A last line that is not a JSON object means the transcript may have been cut off
    while being written, and lost its final reply: the text before it is not that reply.

    Args:
        path: The transcript file.
        limit: Read only the first bytes, up to the last whole line among them; None for all.
    """
    text = read_text(path) if limit is None else _head(path, limit)
    numbered = [
        (n, _entry(line)) for n, line in enumerate(text.split("\n"), start=1) if line.strip()
    ]
    cut_off = ""
    if numbered and numbered[-1][1] is None:
        cut_off = (
            f"the last line of {path} (line {numbered[-1][0]}) is not a readable entry: the "
            "transcript may have been cut off before its final reply"
        )
    return Lines(
        [e for _, e in numbered if e is not None],
        tuple(n for n, e in numbered if e is None),
        cut_off,
    )


def reply_text(t: Transcript) -> str:
    """The transcript's reply. One that cannot be read raises MalformedReply (recorded as
    rejected); no reply at all is an error, never a guess.

    Args:
        t: The transcript, as its format's adapter read it.
    """
    if t.reply_problem:
        raise MalformedReply(t.reply_problem)
    if t.reply is None:
        raise EvalError(f"no assistant reply found in {t.path}")
    return t.reply
