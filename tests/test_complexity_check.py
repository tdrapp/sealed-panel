"""The complexity ceiling: a function may not grow past its frozen baseline."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "complexity_check.py"


@pytest.fixture(scope="module")
def tool() -> ModuleType:
    pytest.importorskip("radon", reason="radon is in the dev group only")
    if not TOOL.exists():  # the mutation check copies src, tests and examples only
        pytest.skip("not a checkout: the tools are not here")
    spec = importlib.util.spec_from_file_location("complexity_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def branches(n: int, name: str = "f") -> str:
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(n))
    return f"def {name}(x):\n{body}    return -1\n"


def test_blocks_are_measured_with_their_closures_and_methods(
    tool: ModuleType, tmp_path: Path
) -> None:
    src = tmp_path / "m.py"
    src.write_text(
        branches(3, "top")
        + "def outer():\n"
        + "\n".join("    " + line for line in branches(2, "inner").splitlines())
        + "\n    return inner\n"
        + "class K:\n"
        + "\n".join("    " + line for line in branches(4, "meth").splitlines())
        + "\n"
    )
    found = tool.measure([src], tmp_path)
    assert found["m.py:top"] == 4
    assert found["m.py:outer.inner"] == 3
    assert found["m.py:K.meth"] == 5


def test_growth_past_the_baseline_fails_and_a_shrink_is_noted(tool: ModuleType) -> None:
    baseline = {"a.py:big": 20, "a.py:gone": 15}
    failures, notes = tool.compare(baseline, {"a.py:big": 21, "a.py:small": 3})
    assert failures == ["a.py:big: complexity 21, ceiling 20"]
    assert notes == ["a.py:gone: in the baseline, no longer in the code"]
    failures, notes = tool.compare(baseline, {"a.py:big": 19, "a.py:gone": 15})
    assert failures == [] and notes == ["a.py:big: complexity 19, below its baseline 20"]


def test_a_new_block_may_not_pass_rank_b(tool: ModuleType) -> None:
    ok, _ = tool.compare({}, {"a.py:new": tool.MAX_NEW})
    grew, _ = tool.compare({}, {"a.py:new": tool.MAX_NEW + 1})
    assert ok == [] and grew == [f"a.py:new: complexity {tool.MAX_NEW + 1}, ceiling 10"]


def test_the_code_stays_within_the_baseline(tool: ModuleType) -> None:
    assert tool.main([]) == 0


def test_a_baseline_that_rises_is_refused(tool: ModuleType) -> None:
    before = {"a.py:f": 20, "a.py:g": 15}
    assert tool.raised(before, {"b.py:f": 20, "a.py:g": 15}) == []  # a move keeps both figures
    assert tool.raised(before, {"a.py:f": 19}) == []  # lowered
    assert tool.raised(None, {"a.py:f": 99}) == []  # the reference had no baseline
    grew = tool.raised(before, {"a.py:f": 21, "a.py:g": 15})
    assert grew == ["a.py:f rose from 20 to 21", "the baseline sums to 36, 35 at the reference"]
    added = tool.raised(before, {"a.py:f": 10, "a.py:g": 11, "a.py:h": 11})
    assert added == ["the baseline holds 3 entries, 2 at the reference"]


def test_a_trade_between_two_functions_is_refused(tool: ModuleType) -> None:
    """Lowering one function by 3 does not buy 3 for another that is in both baselines."""
    before = {"a.py:f": 20, "a.py:g": 15}
    assert tool.raised(before, {"a.py:f": 17, "a.py:g": 18}) == ["a.py:g rose from 15 to 18"]


def test_a_moved_function_fits_the_budget(tool: ModuleType) -> None:
    """A move or rename is one entry removed and one added, of the same value; deleting a
    baselined function pays for a new one of no higher value."""
    before = {"a.py:f": 20, "a.py:g": 15}
    assert tool.raised(before, {"b.py:f": 20, "a.py:g": 15}) == []
    assert tool.raised(before, {"a.py:f": 20, "c.py:new": 15}) == []
    assert tool.raised(before, {"a.py:f": 20, "c.py:new": 16}) == [
        "the baseline sums to 36, 35 at the reference"
    ]


def test_the_reference_baseline_is_read_from_git(tool: ModuleType, tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    (tmp_path / "README").write_text("x\n")
    git("add", "README")
    git("commit", "-q", "-m", "first")
    assert tool.baseline_at("HEAD", tmp_path) is None  # before the baseline existed
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "complexity_baseline.json").write_text('{"a.py:f": 12}\n')
    git("add", "tools")
    git("commit", "-q", "-m", "second")
    assert tool.baseline_at("HEAD", tmp_path) == {"a.py:f": 12}
    with pytest.raises(RuntimeError, match="not a commit"):
        tool.baseline_at("no-such-ref", tmp_path)
