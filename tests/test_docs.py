"""The prose docs stay within the wrap width, so a stray long line or a broken re-wrap shows."""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WIDTH = 96
# Texts that are judged, not read as docs: they keep one paragraph per line, as written.
JUDGED_TEXTS = {"examples/announcement/control.md", "examples/announcement/draft_v1.md",
                "examples/announcement/facts.md"}  # fmt: skip


def markdown_files() -> list[Path]:
    if not (ROOT / ".git").exists():  # the mutation check runs on a copy without the docs
        pytest.skip("not a git checkout: the docs are not here")
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "*.md"], capture_output=True, text=True, check=True
    ).stdout.split()
    return [
        ROOT / rel
        for rel in out
        if not rel.startswith("tests/fixtures/") and rel not in JUDGED_TEXTS
    ]


def long_lines(text: str) -> list[int]:
    """Line numbers over WIDTH, outside front matter, code blocks and table rows. A line with no
    space (a badge, a long link) cannot wrap and is skipped."""
    lines = text.splitlines()
    skip = 0
    if lines and lines[0] == "---" and "---" in lines[1:]:
        skip = lines.index("---", 1) + 1  # front matter: the skill's description is one line
    found, fence = [], False
    for n, line in enumerate(lines, 1):
        if n <= skip:
            continue
        if line.startswith("```"):
            fence = not fence
        elif not fence and not line.startswith("|") and " " in line and len(line) > WIDTH:
            found.append(n)
    return found


def test_markdown_lines_fit_the_wrap_width() -> None:
    files = markdown_files()
    assert len(files) > 10
    over = {str(p.relative_to(ROOT)): long_lines(p.read_text(encoding="utf-8")) for p in files}
    assert {k: v for k, v in over.items() if v} == {}


def test_the_check_sees_prose_and_skips_the_rest() -> None:
    long = "a " * WIDTH
    text = f"---\nd: {long}\n---\nok\n{long}\n```\n{long}\n```\n| {long} |\n{'x' * 200}\n"
    assert long_lines(text) == [5]


def test_the_readme_mutation_figures_match_the_tool() -> None:
    """The README states how many mutations the tool holds, how many are killed and how many
    survive; a mutation added or an expected survivor listed without the README fails here."""
    tool, readme = ROOT / "tools" / "mutation_check.py", ROOT / "README.md"
    if not (tool.exists() and readme.exists()):  # the mutation check runs on a copy without them
        pytest.skip("not a checkout: the README and the mutation tool are not here")
    spec = importlib.util.spec_from_file_location("mutation_check", tool)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    total, survivors = len(module.MUTATIONS), len(module.EXPECTED_SURVIVORS)
    text = " ".join(readme.read_text(encoding="utf-8").split())
    found = re.search(r"Of its (\d+) mutations, (\d+) are killed; the (\w+) that survive", text)
    assert found, "the README no longer states the mutation figures in the expected sentence"
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    assert (int(found[1]), int(found[2]), found[3]) == (
        total,
        total - survivors,
        words.get(survivors, str(survivors)),
    )
