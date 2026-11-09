"""File helpers shared by every step: JSON in and out, text in, round directories."""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from .errors import EvalError


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


def _open_dir(parent: Path) -> int:
    """The directory a file goes in, opened without following a link in its last step."""
    try:
        return os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        if parent.is_symlink():
            raise EvalError(
                f"{parent} is a symbolic link: a run writes only inside its own directory"
            ) from exc
        raise


def _modes(existing: os.stat_result | None) -> tuple[int, int | None]:
    """(mode to create the draft with, mode to set on it). Replacing a file: the draft starts
    private and takes the file's mode, so a 0600 file is never exposed by its draft. A new
    file: created as open() would, the umask applied."""
    if existing is not None and stat.S_ISREG(existing.st_mode):
        return 0o600, stat.S_IMODE(existing.st_mode)
    return 0o666, None


def write_file(path: Path, text: str) -> None:
    """Write a file whole or not at all, and never through a link: a temporary file in the
    target directory, moved over the target, both named from an open directory. A reader in
    another call never sees half a file; a link planted on the target is replaced, not followed,
    and a link planted on the directory stops the write. An existing file keeps its mode."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    if os.open not in os.supports_dir_fd:  # no directory descriptors on this platform
        refuse_links(path.parent)
        temporary = path.parent / draft
        try:
            existing = path.lstat() if path.exists() or path.is_symlink() else None
        except FileNotFoundError:
            existing = None
        try:
            create, final = _modes(existing)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, create)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            if final is not None:
                os.chmod(temporary, final)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return
    folder = _open_dir(path.parent)
    try:
        try:
            before: os.stat_result | None = os.stat(path.name, dir_fd=folder, follow_symlinks=False)
        except FileNotFoundError:
            before = None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        create, final = _modes(before)
        fd = os.open(draft, flags, create, dir_fd=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                if final is not None:
                    os.fchmod(f.fileno(), final)
            os.replace(draft, path.name, src_dir_fd=folder, dst_dir_fd=folder)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(draft, dir_fd=folder)
    finally:
        os.close(folder)


def read_no_follow(path: Path) -> bytes:
    """A regular file's bytes, refused when the path is a symbolic link or anything but a
    regular file (a FIFO would block the open). On platforms without these flags, plain."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if path.is_symlink():
            raise EvalError(f"{path} is a symbolic link: refused") from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise EvalError(f"{path} is not a regular file: refused")
        if hasattr(os, "set_blocking"):
            os.set_blocking(fd, True)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as f:
        return f.read()


def read_text(path: Path) -> str:
    """A file's text, read as UTF-8.

    Args:
        path: The file.
    """
    return path.read_text(encoding="utf-8")


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
