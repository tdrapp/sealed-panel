"""The mutation check's own rules: how a pytest exit code is read, how mutations are split
into shards, that a survivor is confirmed on the whole suite, and how a coverage context names
its test."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOL = Path(__file__).resolve().parent.parent / "tools" / "mutation_check.py"


@pytest.fixture(scope="module")
def mc() -> ModuleType:
    if not TOOL.exists():  # the mutation check runs on a copy without the tools
        pytest.skip("not a checkout: the mutation tool is not here")
    spec = importlib.util.spec_from_file_location("mutation_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExitCodes:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [(0, "survived"), (1, "killed"), (2, "interrupt"), (None, "timeout")]
        + [(code, "error") for code in (3, 4, 5, -9, 137)],
    )
    def test_each_exit_code_has_one_reading(
        self, mc: ModuleType, code: int | None, expected: str
    ) -> None:
        assert mc.outcome(code) == expected

    @pytest.mark.slow  # starts pytest in a subprocess
    @pytest.mark.parametrize(
        ("files", "tests", "code", "expected"),
        [
            ({"test_a.py": "def test_ok():\n    pass\n"}, None, 0, "survived"),
            ({"test_a.py": "def test_no():\n    assert False\n"}, None, 1, "killed"),
            ({"test_a.py": "def test_c():\n    raise KeyboardInterrupt\n"}, None, 2, "interrupt"),
            ({"test_a.py": "import no_such_module_here\n"}, None, 2, "interrupt"),
            (
                {"conftest.py": "def pytest_collection_finish(session):\n    raise OSError\n"},
                None,
                3,
                "error",
            ),
            ({"test_a.py": "def test_ok():\n    pass\n"}, ["tests/test_a.py::gone"], 4, "error"),
            ({"test_a.py": "X = 1\n"}, None, 5, "error"),
        ],
    )
    def test_real_pytest_runs_are_read_by_their_exit_code(
        self,
        mc: ModuleType,
        tmp_path: Path,
        files: dict[str, str],
        tests: list[str] | None,
        code: int,
        expected: str,
    ) -> None:
        """A usage error (a node id the copy lacks), an internal error or an empty collection
        is never a kill; an interrupt is, flagged as such."""
        (tmp_path / "tree" / "tests").mkdir(parents=True)
        for name, text in files.items():
            (tmp_path / "tree" / "tests" / name).write_text(text)
        run = mc._pytest(tmp_path, tests, 60)
        assert (run.code, mc.outcome(run.code)) == (code, expected)

    def test_an_error_fails_the_check(self, mc: ModuleType) -> None:
        detected = {n: "killed" for n in mc.MUTATIONS if n not in mc.EXPECTED_SURVIVORS}
        survivors = dict.fromkeys(mc.EXPECTED_SURVIVORS, "survived")
        assert mc.judge({**detected, **survivors}) == 0
        assert mc.judge({**detected, **survivors, next(iter(detected)): "error"}) == 1
        assert mc.judge({**detected, **survivors, next(iter(detected)): "timeout"}) == 0
        assert mc.judge({**detected, **survivors, next(iter(detected)): "interrupt"}) == 0


class TestAnchorsOnly:
    def test_every_anchor_is_found_exactly_once(self, mc: ModuleType) -> None:
        assert mc.anchor_problems(mc.MUTATIONS) == []
        assert mc.main(["--anchors-only"]) == 0

    def test_a_missing_or_repeated_anchor_fails_without_running_a_test(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def no_pytest(*_: Any) -> Any:
            raise AssertionError("--anchors-only runs no test")

        monkeypatch.setattr(mc, "_pytest", no_pytest)
        monkeypatch.setitem(mc.MUTATIONS, "gone", (mc.REC, "no such text anywhere", "x"))
        monkeypatch.setitem(mc.MUTATIONS, "twice", (mc.REC, "\n\n", "x"))
        assert mc.main(["--anchors-only"]) == 1
        problems = mc.anchor_problems(["gone", "twice"])
        assert problems[0] == f"gone: anchor found 0 times in {mc.REC}"
        assert problems[1].startswith("twice: anchor found ")


class TestShards:
    @pytest.mark.parametrize("n", [1, 2, 4, 7])
    def test_the_shards_partition_the_mutations(self, mc: ModuleType, n: int) -> None:
        shards = [{m for m in mc.MUTATIONS if mc.in_shard(m, k, n)} for k in range(n)]
        assert set().union(*shards) == set(mc.MUTATIONS)
        assert sum(len(s) for s in shards) == len(mc.MUTATIONS)

    def test_a_mutation_missing_from_the_merged_reports_fails(
        self, mc: ModuleType, tmp_path: Path
    ) -> None:
        results = [
            {"name": n, "outcome": "survived" if n in mc.EXPECTED_SURVIVORS else "killed"}
            for n in mc.MUTATIONS
        ]
        whole, part = tmp_path / "whole.json", tmp_path / "part.json"
        whole.write_text(json.dumps({"results": results}))
        part.write_text(json.dumps({"results": results[1:]}))
        assert mc.merge([whole]) == 0
        assert mc.merge([part]) == 1
        assert mc.merge([whole, whole]) == 1


class TestSurvivors:
    def fake(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch, full_code: int
    ) -> list[list[str] | None]:
        calls: list[list[str] | None] = []

        def run(work: Path, tests: list[str] | None, timeout: float, *_: Any) -> Any:
            calls.append(tests)
            return mc.Run(full_code if tests is None else 0, 0.1)

        monkeypatch.setattr(mc, "_pytest", run)
        return calls

    def test_a_survivor_of_the_selection_is_run_on_the_whole_suite(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        calls = self.fake(mc, monkeypatch, full_code=0)
        name = next(iter(mc.MUTATIONS))
        checker = mc.Checker(tmp_path, {name: mc.Selection(["tests/test_x.py::test_a"])}, {})
        r = checker.check(name)
        assert calls == [["tests/test_x.py::test_a"], None]
        assert r["outcome"] == "survived" and r["confirmed"]

    def test_the_whole_suite_can_still_kill_it(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self.fake(mc, monkeypatch, full_code=1)
        name = next(iter(mc.MUTATIONS))
        checker = mc.Checker(tmp_path, {name: mc.Selection(["tests/test_x.py::test_a"])}, {})
        assert checker.check(name)["outcome"] == "killed"

    @pytest.mark.parametrize("code", [1, 2])
    def test_a_kill_by_tests_that_fail_unmutated_is_an_error(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int
    ) -> None:
        monkeypatch.setattr(mc, "_pytest", lambda *a, **k: mc.Run(code, 0.1, ["t::a"]))
        name = next(iter(mc.MUTATIONS))
        checker = mc.Checker(tmp_path, {name: mc.Selection(["t::a", "t::b"])}, {})
        assert checker.check(name)["outcome"] == "error"


class TestTimeouts:
    """A timeout counts as detected only after a run alone and only when the same tests finish
    within the same limit on unmutated code; on the whole-suite re-run of a survivor it is an
    error."""

    TESTS = ("tests/test_x.py::test_a",)

    def checker(
        self, mc: ModuleType, tmp_path: Path, codes: dict[tuple[bool, bool], list[int | None]]
    ) -> tuple[Any, list[tuple[str | None, list[str] | None]]]:
        """codes maps (mutated, whole suite) to the exit codes its runs return, in order."""
        name = next(iter(mc.MUTATIONS))
        # Each selected test took 10 s in the map run: its limit is max(60, 5 x 10 + 30) = 80 s.
        durations = dict.fromkeys(self.TESTS, 10.0)
        checker = mc.Checker(tmp_path, {name: mc.Selection(list(self.TESTS))}, durations)
        calls: list[tuple[str | None, list[str] | None]] = []
        checker.limits = []

        def run(mutation: str | None, tests: list[str] | None, timeout: float) -> Any:
            calls.append((mutation, tests))
            checker.limits.append(timeout)
            return mc.Run(codes[(mutation is not None, tests is None)].pop(0), 0.1)

        checker._run = run
        return checker, calls

    def test_a_timeout_on_the_whole_suite_rerun_is_an_error(
        self, mc: ModuleType, tmp_path: Path
    ) -> None:
        checker, _ = self.checker(mc, tmp_path, {(True, False): [0], (True, True): [None]})
        r = checker.check(next(iter(mc.MUTATIONS)))
        assert r["outcome"] == "error" and r["confirmed"]

    def test_a_timeout_is_run_again_alone_then_checked_unmutated(
        self, mc: ModuleType, tmp_path: Path
    ) -> None:
        name = next(iter(mc.MUTATIONS))
        checker, calls = self.checker(
            mc, tmp_path, {(True, False): [None, None], (False, False): [0]}
        )
        assert checker.check(name)["outcome"] == "timeout"
        r = checker.retry_timeout(name)
        assert r["outcome"] == "timeout" and "run again alone" in r["note"]
        assert calls == [(name, [*self.TESTS]), (name, [*self.TESTS]), (None, [*self.TESTS])]
        # The unmutated run gets the very limit the mutated runs had, not a longer one.
        assert checker.limits == [80.0, 80.0, 80.0]

    def test_main_runs_a_timed_out_mutation_through_the_retry(
        self, mc: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        name = next(iter(mc.MUTATIONS))
        seen: list[str] = []

        class FakeChecker:
            def __init__(self, *args: Any) -> None:
                pass

            def check(self, mutation: str) -> dict[str, Any]:
                seen.append("check")
                return {"name": mutation, "outcome": "timeout", "selected": 1, "note": ""}

            def retry_timeout(self, mutation: str) -> dict[str, Any]:
                seen.append("retry")
                return {"name": mutation, "outcome": "timeout", "selected": 1, "note": "again"}

        monkeypatch.setattr(mc, "_copy_tree", lambda dest: None)
        monkeypatch.setattr(mc, "_imports_copy", lambda dest: True)
        monkeypatch.setattr(mc, "build_map", lambda work, names: ({}, mc.Run(0, 0.1)))
        monkeypatch.setattr(mc, "Checker", FakeChecker)
        monkeypatch.setattr(mc, "_line", lambda r: r["outcome"])
        mc.main(["--only", name])
        assert seen == ["check", "retry"]

    def test_a_timeout_that_does_not_repeat_takes_the_new_verdict(
        self, mc: ModuleType, tmp_path: Path
    ) -> None:
        checker, _ = self.checker(mc, tmp_path, {(True, False): [None, 1], (False, False): [0]})
        name = next(iter(mc.MUTATIONS))
        checker.check(name)
        assert checker.retry_timeout(name)["outcome"] == "killed"

    @pytest.mark.parametrize("clean", [None, 1])
    def test_a_timeout_is_an_error_when_unmutated_code_does_not_fit_the_limit(
        self, mc: ModuleType, tmp_path: Path, clean: int | None
    ) -> None:
        checker, _ = self.checker(
            mc, tmp_path, {(True, False): [None, None], (False, False): [clean]}
        )
        name = next(iter(mc.MUTATIONS))
        checker.check(name)
        assert checker.retry_timeout(name)["outcome"] == "error"


class TestMap:
    def test_a_context_is_split_on_its_last_bar(self, mc: ModuleType) -> None:
        assert mc.split_context("tests/t.py::test_a|run") == "tests/t.py::test_a"
        assert (
            mc.split_context("tests/t.py::test_a[x | y-z]|setup") == "tests/t.py::test_a[x | y-z]"
        )

    def test_a_statement_is_mapped_by_its_first_line(self, mc: ModuleType) -> None:
        src = "def f(x):\n    y = g(\n        x,\n    )\n    if y:\n        return 1\n"
        assert mc._anchor_lines(src, "        x,\n") == {2, 3}
        assert mc._anchor_lines(src, "        return 1\n") == {6}
        assert mc._anchor_lines(src, "    if y:\n") == {5}

    def test_the_timeout_has_a_floor_and_scales(self, mc: ModuleType) -> None:
        assert mc.timeout_for(0.5) == 60
        assert mc.timeout_for(20) == 130
