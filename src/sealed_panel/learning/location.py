"""Where learning may write: never in a git working tree."""

from __future__ import annotations

from pathlib import Path

from ..errors import ConfigError
from ..templates import LIBRARY_ENV


def _git_top(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def writable_library(lib: Path) -> Path:
    """The library, refused for a write when it lies in a git working tree (a folder with a
    `.git` at or above it, ignored by git or not: no git process runs to ask). Candidates and
    their history quote the judged texts, so they are never written where a commit could take
    them. Reads never refuse.

    Args:
        lib: The library, as library_dir resolved it.
    """
    top = _git_top(lib.resolve())
    if top is not None:
        raise ConfigError(
            f"the rubric library {lib} is inside the git working tree {top}: candidates quote "
            "the judged texts, so they are never written there. Point --library or "
            f"${LIBRARY_ENV} at a folder outside any repository"
        )
    return lib
