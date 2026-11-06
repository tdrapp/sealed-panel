"""File helpers shared by every step: JSON in and out, text in, round directories."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from .errors import EvalError


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path} is not valid JSON: {exc}") from exc


def dump_json(path: Path, data: Any) -> None:
    """Write whole or not at all: a reader in another call never sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    draft = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    draft.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(draft, path)


def read_text(path: Path) -> str:
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
