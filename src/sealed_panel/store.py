"""File helpers shared by every step: JSON in and out, text in, round directories, the one
containment guard for paths a caller gives, and the locks (one per seat, one per run).

Lock order: a call that holds both takes its seat locks first, then the run lock, never the
reverse, so two calls never wait on each other."""

from __future__ import annotations

import contextlib
import fcntl
import functools
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

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


def make_dir(path: Path) -> None:
    """Create a directory and every missing parent owner-only (0700), as every directory of a
    run is.

    Args:
        path: The directory.
    """
    missing = []
    while not path.exists():
        missing.append(path)
        path = path.parent
    for folder in reversed(missing):
        folder.mkdir(mode=0o700, exist_ok=True)


def write_file(path: Path, text: str) -> None:
    """Write a file whole or not at all: a private temporary file in the target directory,
    then moved over the target. A reader in another call never sees half a file, and a link on
    the target is replaced, never written through. Every file is written owner-only (0600).

    Args:
        path: The file.
        text: Its whole content.
    """
    make_dir(path.parent)
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


def contained(path: Path, root: Path, given: str | None = None) -> Path:
    """The path with every link followed, refused unless it stays under the root: the one guard
    on a path a caller gives (an MCP tool argument, a spec's facts_file), so a `..` or a link
    that leaves the root never turns into a read or a write outside it.

    Args:
        path: The path given, absolute or relative to the current directory.
        root: The folder it must stay under.
        given: How the refusal names the path (the argument as the caller wrote it).
    """
    real = path.resolve()
    if not real.is_relative_to(root.resolve()):
        name = given if given is not None else repr(str(path))
        raise EvalError(f"{name} is outside the root {root}: every path stays under it")
    return real


@contextlib.contextmanager
def locked(path: Path, busy: str, wait: bool = False) -> Iterator[None]:
    """Hold an exclusive lock on `path` (fcntl.flock) while the block runs. Without `wait`,
    refuse at once with SeatTaken(busy) when another call holds it; with it, wait for the
    holder to finish. The kernel releases the lock when its holder's process dies, so a dead
    holder never blocks the next call. A filesystem without flock is refused: nothing is
    recorded unlocked.

    Args:
        path: The lock file, created owner-only when absent.
        busy: The refusal when another call holds the lock.
        wait: Wait for the lock instead of refusing.
    """
    how = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise EvalError(f"cannot open the lock file {path} ({exc.strerror}): refused") from exc
    try:
        try:
            fcntl.flock(fd, how)
        except BlockingIOError as exc:
            raise SeatTaken(busy) from exc
        except OSError as exc:
            raise EvalError(
                f"cannot lock {path} ({exc.strerror}): this filesystem does not support file "
                "locks (flock), and nothing is recorded unlocked; keep the run on a local disk"
            ) from exc
        yield
    finally:
        os.close(fd)


def run_lock(run: Path) -> contextlib.AbstractContextManager[None]:
    """The run's own lock (`run.lock`), waited for: held briefly by the steps that read and
    write what spans the whole run (the agent ids and replies already used, the briefs of a
    round, the aggregate), so two concurrent calls never both pass a run-wide check. A call
    that holds seat locks takes it after them.

    Args:
        run: The run directory.
    """
    return locked(run / "run.lock", "another call is changing this run", wait=True)


P = ParamSpec("P")
R = TypeVar("R")


def under_run_lock(fn: Callable[Concatenate[Path, P], R]) -> Callable[Concatenate[Path, P], R]:
    """Run `fn(run, ...)` under the run's lock (see run_lock)."""

    @functools.wraps(fn)
    def held(run: Path, /, *args: P.args, **kwargs: P.kwargs) -> R:
        with run_lock(run):
            return fn(run, *args, **kwargs)

    return held


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
