"""File helpers shared by every step: JSON in and out, text in, round directories, the one
containment guard for paths a caller gives, and the per-seat lock."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .errors import EvalError, SeatTaken


def load_json(path: Path) -> Any:
    """The JSON in a file, refused when it is not valid JSON.

    Args:
        path: The file.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path} is not valid JSON: {exc}") from exc


def dump_json(path: Path, data: Any) -> None:
    """Write data as indented JSON, through the one file-writing helper.

    Args:
        path: The file.
        data: What to write.
    """
    write_file(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def write_file(path: Path, text: str) -> None:
    """Write a file whole or not at all: a private temporary file in the target directory,
    then moved over the target. A reader in another call never sees half a file, and a link on
    the target is replaced, never written through. Every file is written owner-only (0600).

    Args:
        path: The file.
        text: Its whole content.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, draft = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(draft, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(draft)


def read_text(path: Path) -> str:
    """A file's text, read as UTF-8.

    Args:
        path: The file.
    """
    return path.read_text(encoding="utf-8")


def contained(path: Path, root: Path) -> Path:
    """The path with every link followed, refused unless it stays under the root: the one guard
    on a path a caller gives (an MCP tool argument, a spec's facts_file), so a `..` or a link
    that leaves the root never turns into a read or a write outside it.

    Args:
        path: The path given, absolute or relative to the current directory.
        root: The folder it must stay under.
    """
    real = path.resolve()
    if not real.is_relative_to(root.resolve()):
        raise EvalError(f"{str(path)!r} is outside the root {root}: every path stays under it")
    return real


@contextlib.contextmanager
def locked(path: Path, busy: str) -> Iterator[None]:
    """Hold an exclusive lock on `path` (fcntl.flock) while the block runs, or refuse at once
    with SeatTaken(busy) when another call holds it. The kernel releases the lock when its
    holder's process dies, so a dead holder never blocks the next call. A filesystem without
    flock is refused: nothing is recorded unlocked.

    Args:
        path: The lock file, created when absent.
        busy: The refusal when another call holds the lock.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SeatTaken(busy) from exc
        except OSError as exc:
            raise EvalError(
                f"cannot lock {path} ({exc.strerror}): this filesystem does not support file "
                "locks (flock), and a seat is never recorded unlocked; keep the run on a local disk"
            ) from exc
        yield
    finally:
        os.close(fd)


def refuse_links(*paths: Path) -> None:
    """Refuse a symbolic link where a run writes: a link planted in a run would send its files
    elsewhere."""
    for p in paths:
        if p.is_symlink():
            raise EvalError(f"{p} is a symbolic link: a run writes only inside its own directory")


def resolve_round(rnd: int | None, sealed: bool) -> int:
    """The round asked for; with no number, the sealed panel's round 1, never a loop round."""
    if rnd is not None:
        return int(rnd)
    if sealed:
        return 1
    raise EvalError("--round is required for a loop round (it defaults to 1 only with --sealed)")


def round_dir(run: Path, rnd: int, sealed: bool = False) -> Path:
    """A round's directory, refused when it or its parent is a symbolic link."""
    parent = run / ("sealed" if sealed else "rounds")
    rdir = parent / f"r{rnd}"
    refuse_links(parent, rdir)
    return rdir
