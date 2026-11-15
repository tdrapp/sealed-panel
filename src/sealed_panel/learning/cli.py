"""The learning side of the command line: `learning learn`, `feedback`, and the inbox of
`rubrics` (`list` shows it, `promote` and `reject` decide on a candidate). The parser stays in
`sealed_panel.cli`, so the commands and their help are where they were."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..errors import EvalError
from ..store import load_json
from ..templates import library_dir
from . import candidates
from .learn import learn_after_seal, user_disagreement


def print_inbox(cands: list[dict[str, Any]]) -> None:
    """Print the open candidates, as `rubrics list` shows them.

    Args:
        cands: The open candidates, from candidates.open_candidates.
    """
    print(f"inbox: {len(cands)} open candidate(s)")
    for c in cands:
        tpl = c.get("template") or {}
        name = f"{tpl.get('id')} v{tpl.get('version')}" if tpl else "-"
        print(f"  {c['id']}  {c['kind']:19} template {name:18} {c['dimension']} (x{c['count']})")
        for before in c.get("rejected_before") or []:
            version = (before.get("template") or {}).get("version")
            print(f"      rejected before (v{version}): {before.get('reason')}")


def _confirm(a: argparse.Namespace, plan: dict[str, object]) -> None:
    for line in candidates.side_by_side(plan["current"], plan["updated"]):  # type: ignore[arg-type]
        print(line)
    print(f"writes {plan['target']}")
    if a.yes:
        return
    if not sys.stdin.isatty():
        raise EvalError("promote needs a person: run it in a terminal, or pass --yes")
    try:
        answer = input(f"type the candidate id ({a.candidate}) to promote it: ").strip()
    except EOFError:
        answer = ""
    if answer != a.candidate:
        raise EvalError("not confirmed: nothing was written")


def promote_command(a: argparse.Namespace, lib: Path) -> None:
    """`rubrics promote`: show the change side by side, have a person confirm it, apply it.

    Args:
        a: The parsed arguments.
        lib: The user library, resolved by library_dir.
    """
    plan = candidates.plan_promotion(lib, a.candidate, a.into)
    _confirm(a, plan)
    path = candidates.apply_promotion(lib, plan)
    print(f"promoted {a.candidate} into {path} (v{load_json(path)['version']})")


def reject_command(a: argparse.Namespace, lib: Path) -> None:
    """`rubrics reject`: close a candidate with a reason.

    Args:
        a: The parsed arguments.
        lib: The user library, resolved by library_dir.
    """
    path = candidates.reject(lib, a.candidate, a.reason)
    print(f"rejected {a.candidate}: moved to {path}")


def feedback_command(a: argparse.Namespace) -> None:
    """`feedback`: record the user's own read disagreeing with the sealed grade.

    Args:
        a: The parsed arguments.
    """
    lib = library_dir(a.library) if a.library else None
    path = user_disagreement(a.run, a.note, a.dimension, lib)
    print(f"recorded your read as candidate {path.stem} in {path.parent}")


def learn_command(a: argparse.Namespace) -> None:
    """`learning learn`: write a sealed run's rubric candidates to the inbox.

    Args:
        a: The parsed arguments.
    """
    lib = library_dir(a.library)
    paths = learn_after_seal(a.run, lib)
    print(f"{len(paths)} rubric candidate(s) in {lib / 'inbox'}")
