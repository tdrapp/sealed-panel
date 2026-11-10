"""The commit-msg hook: a Conventional Commits subject under 72 characters."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "check_commit_msg.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    if not TOOL.exists():  # the mutation check copies src, tests and examples only
        pytest.skip("not a checkout: the tools are not here")
    spec = importlib.util.spec_from_file_location("check_commit_msg", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "message",
    [
        "fix: reject a transcript cut off on its last line\n",
        "feat(mcp)!: drop the old tool names\n\nThe body says why.\n",
        "docs: x\n# Please enter the commit message\n",
        "fixup! fix: reject a transcript cut off on its last line\n",
        "ci: " + "x" * 67 + "\n",
    ],
)
def test_a_conventional_subject_passes(tool: ModuleType, message: str) -> None:
    assert tool.problems(message) == []


@pytest.mark.parametrize(
    ("message", "why"),
    [
        ("Fix the transcript reader\n", "not `type: summary`"),
        ("feature: add a thing\n", "not `type: summary`"),
        ("fix:no space\n", "not `type: summary`"),
        ("fix: \n", "not `type: summary`"),
        ("ci: " + "x" * 68 + "\n", "72 characters; keep it under 72"),
        ("fix: a subject\nno blank line\n", "blank line"),
        ("", "not `type: summary`"),
    ],
)
def test_a_subject_off_the_rule_is_refused(tool: ModuleType, message: str, why: str) -> None:
    assert any(why in p for p in tool.problems(message))


def test_the_hook_reads_the_message_file(tool: ModuleType, tmp_path: Path) -> None:
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.write_text("test: an HTTP judge failure ends as a void\n")
    bad.write_text("WIP\n")
    assert tool.main([str(good)]) == 0
    assert tool.main([str(bad)]) == 1
    assert tool.main([]) == 2
