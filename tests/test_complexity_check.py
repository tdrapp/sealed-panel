"""The complexity ceiling: a function may not grow past its frozen baseline."""

from __future__ import annotations

import importlib.util
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
