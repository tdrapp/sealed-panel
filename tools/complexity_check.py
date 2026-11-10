"""Complexity ceiling: no function grows more complex than it is in the frozen baseline.

xenon (run in CI) caps the ranks: the average, each module's average, the worst block. Ranks
are coarse, and the worst blocks are already at the last rank, so this check freezes each
block above rank B at its current cyclomatic complexity, as radon measures it. A block not in
the baseline must stay at rank B or better (at most MAX_NEW); a block in it must not grow. A
block that shrank is reported, so the baseline can be lowered with --update.

--update rewrites the baseline from the code, so on its own it could raise the baseline to let
growth in. --against REF closes that: it fails when the baseline holds more entries, or a
higher sum of complexities, than the baseline at the git ref REF (CI passes the pull request's
base). Moving or renaming a function keeps both figures, so it passes; growth does not.

    python tools/complexity_check.py                    # check against complexity_baseline.json
    python tools/complexity_check.py --update           # rewrite the baseline from the code
    python tools/complexity_check.py --against origin/main
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from radon.complexity import cc_visit

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "complexity_baseline.json"
SOURCES = ("src", "tools", "examples")
# The highest complexity of rank B: a new block may not go past it.
MAX_NEW = 10


def _blocks(name: str, block: Any) -> Iterator[tuple[str, int]]:
    """A block and its closures (and a class's methods), with their qualified names."""
    yield name, int(block.complexity)
    for inner in getattr(block, "closures", []):
        yield from _blocks(f"{name}.{inner.name}", inner)
    for method in getattr(block, "methods", []):
        yield from _blocks(f"{name}.{method.name}", method)


def measure(files: Iterable[Path], root: Path = ROOT) -> dict[str, int]:
    """The complexity of every function, method and closure, keyed `path:qualified.name`.

    Args:
        files: The Python files to measure.
        root: The directory the keys' paths are relative to.

    Returns:
        The complexity of each block; a name seen twice in a file keeps its highest value.
    """
    found: dict[str, int] = {}
    for path in files:
        rel = path.relative_to(root).as_posix()
        for block in cc_visit(path.read_text(encoding="utf-8")):
            if getattr(block, "is_method", False):
                continue  # measured through its class
            for name, value in _blocks(block.name, block):
                key = f"{rel}:{name}"
                found[key] = max(value, found.get(key, 0))
    return found


def compare(baseline: dict[str, int], current: dict[str, int]) -> tuple[list[str], list[str]]:
    """What grew past the baseline, and what shrank below it.

    Args:
        baseline: The frozen complexity of each block above rank B.
        current: The complexity of each block now.

    Returns:
        The failures (a block above its baseline, or a new block past MAX_NEW) and the notes
        (a baselined block now below its baseline, or gone).
    """
    failures, notes = [], []
    for key, value in sorted(current.items()):
        ceiling = baseline.get(key, MAX_NEW)
        if value > ceiling:
            failures.append(f"{key}: complexity {value}, ceiling {ceiling}")
        elif key in baseline and value < baseline[key]:
            notes.append(f"{key}: complexity {value}, below its baseline {baseline[key]}")
    for key in sorted(set(baseline) - set(current)):
        notes.append(f"{key}: in the baseline, no longer in the code")
    return failures, notes


def raised(before: dict[str, int] | None, now: dict[str, int]) -> list[str]:
    """How the baseline rose against an earlier one: more entries, or a higher sum.

    Args:
        before: The baseline at the reference, or None when it had none.
        now: The baseline now.

    Returns:
        One line per figure that rose; empty when neither did, or with no earlier baseline.
    """
    if before is None:
        return []
    out = []
    if len(now) > len(before):
        out.append(f"the baseline holds {len(now)} entries, {len(before)} at the reference")
    if sum(now.values()) > sum(before.values()):
        out.append(
            f"the baseline sums to {sum(now.values())}, {sum(before.values())} at the reference"
        )
    return out


def baseline_at(ref: str, root: Path = ROOT) -> dict[str, int] | None:
    """The baseline committed at a git ref, or None when the ref has no baseline file.

    Args:
        ref: A git ref or commit.
        root: The repository.
    """
    rel = BASELINE.relative_to(ROOT).as_posix()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "-C", str(root), *args]
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    if git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode != 0:
        raise RuntimeError(f"{ref} is not a commit here: fetch it first")
    if git("cat-file", "-e", f"{ref}:{rel}").returncode != 0:
        return None  # the reference predates the baseline
    done = git("show", f"{ref}:{rel}")
    if done.returncode != 0:
        raise RuntimeError(f"git show {ref}:{rel} failed: {done.stderr.strip()}")
    data: dict[str, int] = json.loads(done.stdout)
    return data


def _check_against(ref: str) -> int:
    """0 when the baseline did not rise against the one at `ref`; 1 when it did; 2 on error."""
    try:
        before = baseline_at(ref)
    except RuntimeError as exc:
        print(f"complexity_check: {exc}", file=sys.stderr)
        return 2
    rose = raised(before, json.loads(BASELINE.read_text(encoding="utf-8")))
    for line in rose:
        print(f"RAISED: {line}")
    return 1 if rose else 0


def _sources(root: Path) -> list[Path]:
    return sorted(p for d in SOURCES for p in (root / d).rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    """Check the code against the baseline, or rewrite the baseline.

    Args:
        argv: The command-line arguments.

    Returns:
        0 when nothing grew, 1 otherwise.
    """
    ap = argparse.ArgumentParser(description="No function may grow more complex.")
    ap.add_argument("--update", action="store_true", help="rewrite the baseline")
    ap.add_argument("--against", metavar="REF", help="refuse a baseline higher than at REF")
    a = ap.parse_args(argv)
    if a.against:
        refused = _check_against(a.against)
        if refused:
            return refused
    current = measure(_sources(ROOT))
    if a.update:
        frozen = {k: v for k, v in sorted(current.items()) if v > MAX_NEW}
        BASELINE.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
        print(f"baseline: {len(frozen)} blocks above complexity {MAX_NEW}")
        return 0
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    failures, notes = compare(baseline, current)
    for line in notes:
        print(f"note: {line}; lower the baseline with --update")
    for line in failures:
        print(f"GREW: {line}")
    print(f"{len(current)} blocks, {len(baseline)} in the baseline, {len(failures)} grew")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
