"""File helpers shared by every step: JSON in and out, text in, round directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import EvalError


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path} is not valid JSON: {exc}") from exc


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def round_dir(run: Path, rnd: int, sealed: bool = False) -> Path:
    return run / ("sealed" if sealed else "rounds") / f"r{rnd}"
