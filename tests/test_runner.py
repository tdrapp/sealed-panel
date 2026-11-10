"""The judge runner: one fresh process per brief, the brief on stdin, the reply on stdout."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel import runner
from sealed_panel.aggregate import aggregate_round, env_changes
from sealed_panel.cli import main
from sealed_panel.config import Config, ConfigError, default_data, load_config, merge, overrides
from sealed_panel.errors import EvalError, SeatTaken
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import MAX_VOIDS, _record_reply, record_from, record_reply, seat_lock
from sealed_panel.report import render_report, status
from sealed_panel.runner import (
    child_env,
    env_hashes,
    judge_round,
    judge_seat,
    open_seats,
)
from sealed_panel.spec import init_run, run_config
from sealed_panel.store import round_dir

# Every test here starts a judge command or a model endpoint.
pytestmark = pytest.mark.slow

STUB = Path(__file__).resolve().parent / "stub_judge.py"
ORPHAN = Path(__file__).resolve().parent / "orphan_judge.py"


def toml_list(items: list[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def init_runner(
    h: Harness,
    *stub_args: str,
    command: list[str] | None = None,
    extra: str = "",
    **spec: Any,
) -> None:
    argv = command if command is not None else [sys.executable, str(STUB), *stub_args]
    cfg = h.tmp / "runner.toml"
    cfg.write_text(f"[judge]\ncommand = {toml_list(argv)}\n{extra}")
    sp = h.tmp / "spec.json"
    sp.write_text(json.dumps(make_spec(**{"judges_per_persona": 2, "max_rounds": 1, **spec})))
    init_run(h.run, sp, load_config(cfg))


def stored(h: Harness, judge: int, sealed: bool = False) -> list[dict[str, Any]]:
    rdir = round_dir(h.run, 1, sealed)
    return [json.loads(f.read_text()) for f in sorted(rdir.glob(f"out_j{judge}_*.json"))]


def logs(h: Harness, sealed: bool = False) -> list[Path]:
    return sorted(round_dir(h.run, 1, sealed).glob("runner_j*.log"))


class TestReplies:
    def test_a_round_judged_by_the_runner_carries_its_proof(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        results = judge_round(h.run, 1, False)
        assert [r["ok"] for r in results] == [True, True]
        rdir = round_dir(h.run, 1)
        for r in results:
            proof = r["runner"]
            brief = (rdir / f"brief_j{r['judge']}.md").read_bytes()
            assert proof["brief_sha256"] == hashlib.sha256(brief).hexdigest()
            assert r["source"] == "runner" and r["agent_id"].startswith("runner-")
            assert proof["argv"] == [sys.executable, str(STUB)]
            assert proof["exit_code"] == 0 and proof["timed_out"] is False
            assert proof["timeout_s"] == 300 and proof["duration_s"] >= 0
            assert (rdir / proof["stderr_log"]).exists()
            raw = stored(h, r["judge"])[0]["raw"]
            assert proof["stdout_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
            # The stub saw exactly the bytes of the brief file.
            retell = next(iter(json.loads(raw)["items"].values()))["retell"]
            assert retell.split()[1] == proof["brief_sha256"]
            assert stored(h, r["judge"])[0]["result"]["runner"] == proof
        entry = aggregate_round(h.run, 1, False)
        assert entry["sources"] == ["runner"]
        assert "read by sealed-panel from the judge command" in render_report(h.run)
        assert any(o.startswith("judge.command = ") for o in status(h.run)["overrides"])

    def test_stderr_goes_to_the_log_never_into_the_reply(self, h: Harness) -> None:
        init_runner(h, "--noise")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["ok"]
        assert "stub noise" in (round_dir(h.run, 1) / r["runner"]["stderr_log"]).read_text()

    def test_the_judge_starts_in_an_empty_directory(self, h: Harness) -> None:
        init_runner(h, "--show-cwd")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        line = next(
            ln
            for ln in (round_dir(h.run, 1) / r["runner"]["stderr_log"]).read_text().splitlines()
            if ln.startswith("CWD ")
        )
        cwd, listing = json.loads(line[4:])
        assert listing == []
        assert Path(cwd).resolve() != Path.cwd().resolve()
        assert h.run.resolve() not in Path(cwd).resolve().parents

    def test_a_sealed_panel_judged_by_the_runner(self, h: Harness) -> None:
        init_runner(h, "--score", "5")
        h.brief(1, "cand", sealed=True)
        assert all(r["ok"] for r in judge_round(h.run, 1, True))
        aggregate_round(h.run, 1, True)
        assert "Grade (sealed panel): clarity 5 (5/5); evidence 5 (5/5)" in render_report(h.run)


class TestEnvironment:
    def test_only_the_minimal_and_allowed_names_reach_the_judge(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SECRET_TOKEN", "do-not-pass")
        monkeypatch.setenv("JUDGE_API_KEY", "allowed")
        monkeypatch.setenv("LANG", "C.UTF-8")
        init_runner(h, "--show-env", extra='timeout_s = 60\nenv_allow = ["JUDGE_API_KEY"]\n')
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        text = (round_dir(h.run, 1) / r["runner"]["stderr_log"]).read_text()
        names = set(json.loads(next(ln for ln in text.splitlines() if ln.startswith("ENV "))[4:]))
        assert "SECRET_TOKEN" not in names and "SEALED_PANEL_RUBRICS" not in names
        assert {"JUDGE_API_KEY", "PATH", "LANG"} <= names
        assert r["runner"]["env_names"] == sorted(names & set(r["runner"]["env_names"]))
        assert "SECRET_TOKEN" not in r["runner"]["env_names"]

    def test_child_env_is_built_from_names_never_copied_whole(self) -> None:
        data = merge(default_data(), {"judge": {"env_allow": ["KEY"]}})
        env = child_env(Config(data), {"PATH": "/bin", "KEY": "k", "OTHER": "x", "HOME": "/h"})
        assert env == {"PATH": "/bin", "HOME": "/h", "KEY": "k"}


class TestFailures:
    def test_a_non_zero_exit_is_a_void_never_a_reply(self, h: Harness) -> None:
        init_runner(h, "--exit", "3")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and not r["ok"] and r["source"] == "runner"
        assert r["errors"] == ["the judge command exited with status 3"]
        assert r["replied"] is True  # it wrote a reply before failing: kept, never read
        assert stored(h, 1)[0]["raw"].startswith('{"items"')
        assert r["runner"]["exit_code"] == 3
        judge_seat(h.run, 1, 2, False)
        with pytest.raises(EvalError, match="never fills a seat"):
            aggregate_round(h.run, 1, False)

    def test_a_timeout_is_a_void(self, h: Harness) -> None:
        init_runner(h, "--sleep", "300", extra="timeout_s = 1\n")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"] == ["the judge command timed out after 1 s"]
        assert r["runner"]["timed_out"] is True
        assert r["runner"]["duration_s"] < 60  # the judge would have slept 300 s

    def test_no_output_is_a_void(self, h: Harness) -> None:
        init_runner(h, "--silent")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and not r["replied"]
        assert r["errors"] == ["the judge command exited with status 0 without writing a reply"]

    def test_a_command_that_cannot_start_records_nothing(self, h: Harness) -> None:
        init_runner(h, command=[str(h.tmp / "no-such-judge")])
        h.brief(1, "cand")
        with pytest.raises(EvalError, match="cannot start the judge command"):
            judge_seat(h.run, 1, 1, False)
        assert results_by_judge(round_dir(h.run, 1)) == {}

    def test_the_report_labels_a_runner_void(self, h: Harness) -> None:
        # The stub fails the first time it sees each marker file, then answers.
        init_runner(h, "--fail-first", str(h.tmp / "first"))
        h.brief(1, "cand")
        assert judge_seat(h.run, 1, 1, False)["void"]
        assert all(r["ok"] for r in judge_round(h.run, 1, False))
        aggregate_round(h.run, 1, False)
        report = render_report(h.run)
        assert "voided by the judge runner: the judge command exited with status 3" in report
        assert "it wrote output, kept unread" in report


class TestRefusals:
    def test_host_mode_has_no_runner(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        with pytest.raises(EvalError, match="no judge command"):
            open_seats(h.run, 1, False)
        with pytest.raises(EvalError, match="no judge command"):
            judge_seat(h.run, 1, 1, False)

    def test_a_run_frozen_without_a_judge_section_is_in_host_mode(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        del data["judge"]
        cfg = Config(data)
        (h.run / "config.json").write_text(json.dumps(data))
        (h.run / "config.lock").write_text(cfg.fingerprint + "\n")
        assert run_config(h.run).judge["command"] == []
        assert not [o for o in overrides(run_config(h.run)) if o.startswith("judge.")]

    def test_a_run_with_a_judge_command_takes_only_runner_replies(self, h: Harness) -> None:
        init_runner(h, "--exit", "3")
        h.brief(1, "cand")
        assert judge_seat(h.run, 1, 1, False)["void"]
        meta = h.keys(1)["judges"][0]
        raw = judge_json(meta, h.texts(1, meta), {r: BAD for r in meta["key"].values()})
        brief = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        t = h.tmp / "t.jsonl"
        rows = [
            {"type": "user", "agentId": "host", "message": {"content": brief}},
            {"type": "assistant", "agentId": "host",
             "message": {"content": [{"type": "text", "text": raw}]}},
        ]  # fmt: skip
        t.write_text("\n".join(json.dumps(x) for x in rows))
        for kwargs in ({"output": raw}, {"transcript": t}, {"transcript": t, "void": True}):
            with pytest.raises(EvalError, match="every reply comes from the runner"):
                record_from(h.run, 1, 2, "host", False, **kwargs)  # type: ignore[arg-type]
        with pytest.raises(EvalError, match="every reply comes from the runner"):
            record_reply(h.run, 1, 2, "host", raw, False, "output")
        assert set(results_by_judge(round_dir(h.run, 1))) == {1}

    def test_a_run_without_a_judge_command_keeps_host_mode(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        assert all(r["ok"] for r in h.answer(1, {"candidate": FLAT, "control": BAD}))

    def test_a_runner_reply_needs_its_proof_and_only_it_has_one(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        with pytest.raises(EvalError, match="runner's proof"):
            _record_reply(h.run, 1, 1, "x1", "{}", False, "runner")
        with pytest.raises(EvalError, match="runner's proof"):
            _record_reply(h.run, 1, 1, "x2", "{}", False, "output", runner={"argv": []})

    def test_the_public_api_cannot_forge_a_runner_reply(self, h: Harness) -> None:
        init_runner(h, command=["/bin/false"])
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": {"clarity": 5, "evidence": 5}}
        raw = judge_json(meta, h.texts(1, meta), sc)
        forged = {"argv": ["/bin/false"], "exit_code": 0, "duration_s": 1.0, "stderr_log": "x"}
        with pytest.raises(TypeError):
            record_reply(h.run, 1, 1, "runner-forged", raw, False, "runner", runner=forged)  # type: ignore[call-arg]
        with pytest.raises(EvalError, match="recorded by `sealed-panel judge` only"):
            record_reply(h.run, 1, 1, "runner-forged", raw, False, "runner")
        assert results_by_judge(round_dir(h.run, 1)) == {}

    def test_a_judge_other_than_the_expected_one_is_refused_before_it_runs(
        self, h: Harness
    ) -> None:
        init_runner(h)
        h.brief(1, "cand")
        expected = {**run_config(h.run).judge, "command": ["/bin/echo", "the user's judge"]}
        with pytest.raises(EvalError, match="differ from the configuration the server"):
            open_seats(h.run, 1, False, None, expected)
        with pytest.raises(EvalError, match="differ from the configuration the server"):
            judge_seat(h.run, 1, 1, False, expected)
        assert logs(h) == []
        same = dict(run_config(h.run).judge)
        assert judge_seat(h.run, 1, 1, False, same)["ok"]

    def test_a_spec_cannot_set_the_judge(self, h: Harness) -> None:
        attempts: list[dict[str, Any]] = [
            {"judge": {"command": ["/bin/sh"]}},
            {"command": ["/bin/sh"]},
            {"config": {"judge": {"command": ["/bin/sh"]}}},
        ]
        for extra in attempts:
            sp = h.tmp / "s.json"
            sp.write_text(json.dumps(make_spec(**extra)))
            with pytest.raises(EvalError, match="unknown key"):
                init_run(h.tmp / f"r{len(extra)}{next(iter(extra))}", sp)

    def test_the_judge_command_is_shown_in_full(self, h: Harness) -> None:
        long = ["/opt/judges/" + "a" * 90, "--flag"]
        init_runner(h, command=long)
        shown = [o for o in status(h.run)["overrides"] if o.startswith("judge.command")]
        assert shown == [f"judge.command = {json.dumps(long)} (default [])"]
        assert json.dumps(long) in render_report(h.run)

    def test_an_aggregated_round_takes_no_runner(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        judge_round(h.run, 1, False)
        aggregate_round(h.run, 1, False)
        with pytest.raises(EvalError, match="already aggregated: the runner"):
            judge_seat(h.run, 1, 1, False)

    def test_only_open_seats_run(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        assert [r["judge"] for r in judge_round(h.run, 1, False, [1])] == [1]
        assert open_seats(h.run, 1, False) == [2]
        assert [r["judge"] for r in judge_round(h.run, 1, False)] == [2]
        with pytest.raises(EvalError, match="already has a valid reply or its seat is held"):
            open_seats(h.run, 1, False)
        with pytest.raises(EvalError, match="already has a valid reply in this round"):
            judge_seat(h.run, 1, 1, False)

    def test_an_unknown_seat_is_refused(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        with pytest.raises(EvalError, match=r"no judge \[7\]"):
            open_seats(h.run, 1, False, [1, 7])
        with pytest.raises(EvalError, match="no judge 7 in"):
            judge_seat(h.run, 1, 7, False)

    def test_the_replacement_cap_is_checked_before_a_process_starts(self, h: Harness) -> None:
        # The stub scores every item 4, the control included: each reply is rejected.
        init_runner(h, "--low", "absent-word", extra="[limits]\nmax_replacements = 0\n")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert not r["ok"] and "control scored" in r["errors"][0]
        before = logs(h)
        with pytest.raises(EvalError, match="rejected 1 times"):
            judge_seat(h.run, 1, 1, False)
        with pytest.raises(EvalError, match="rejected 1 times"):
            judge_round(h.run, 1, False)
        assert logs(h) == before  # no process was started

    def test_a_later_seat_at_its_cap_stops_the_round_before_any_process(self, h: Harness) -> None:
        init_runner(h, "--low", "absent-word", extra="[limits]\nmax_replacements = 0\n")
        h.brief(1, "cand")
        assert not judge_seat(h.run, 1, 2, False)["ok"]
        before = logs(h)
        with pytest.raises(EvalError, match="judge 2 was rejected 1 times"):
            judge_round(h.run, 1, False)
        assert logs(h) == before
        assert 1 not in results_by_judge(round_dir(h.run, 1))

    def test_the_void_cap_is_checked_before_a_process_starts(self, h: Harness) -> None:
        init_runner(h, "--exit", "1")
        h.brief(1, "cand")
        for _ in range(MAX_VOIDS):
            assert judge_seat(h.run, 1, 1, False)["void"]
        before = logs(h)
        with pytest.raises(EvalError, match="voided attempts"):
            judge_seat(h.run, 1, 1, False)
        with pytest.raises(EvalError, match="voided attempts"):
            judge_round(h.run, 1, False)
        assert logs(h) == before

    def test_an_identical_reply_counts_once_and_its_process_ends_recorded(self, h: Harness) -> None:
        init_runner(h, "--low", "absent-word", "--fixed")
        h.brief(1, "cand")
        assert not judge_seat(h.run, 1, 1, False)["ok"]
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"][0].startswith("the reply was refused: this exact reply")
        assert len(results_by_judge(round_dir(h.run, 1))[1]) == 2
        assert not list(round_dir(h.run, 1).glob("started_j*"))


class TestSettings:
    @pytest.mark.parametrize(
        ("override", "message"),
        [
            ({"timeout_s": 0}, "judge.timeout_s must be between 1 and 3600"),
            ({"timeout_s": 3601}, "judge.timeout_s must be between 1 and 3600"),
            ({"command": ["python", ""]}, "judge.command must be a list of non-empty strings"),
            ({"command": [1]}, "judge.command must be a list of non-empty strings"),
            ({"env_allow": ["NOT-A-NAME"]}, "judge.env_allow must hold environment variable"),
            ({"env_allow": [3]}, "judge.env_allow must hold environment variable"),
        ],
    )
    def test_bounds(self, override: dict[str, Any], message: str) -> None:
        with pytest.raises(ConfigError, match=message):
            Config(merge(default_data(), {"judge": override}))

    def test_the_timeout_bounds_are_inclusive(self) -> None:
        for value in (1, 3600):
            Config(merge(default_data(), {"judge": {"timeout_s": value}}))


class TestCommandLine:
    def test_judge_prints_one_line_per_seat(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        init_runner(h)
        h.brief(1, "cand")
        assert main(["judge", str(h.run), "--round", "1"]) == 0
        out = capsys.readouterr().out.splitlines()
        assert [ln.split(" (")[0] for ln in out] == ["VALID judge 1", "VALID judge 2"]
        assert "stderr in runner_j1_runner-" in out[0]

    def test_judge_prints_a_void(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        init_runner(h, "--exit", "2")
        h.brief(1, "cand")
        assert main(["judge", str(h.run), "--round", "1", "--judge", "2"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("VOID judge 2: the judge command exited with status 2 (")

    def test_aggregate_counts_voids_with_the_rejected(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        init_runner(h, "--fail-first", str(h.tmp / "first"))
        h.brief(1, "cand")
        assert judge_seat(h.run, 1, 1, False)["void"]
        judge_round(h.run, 1, False)
        capsys.readouterr()
        assert main(["aggregate", str(h.run), "--round", "1"]) == 0
        assert "1 rejected or void attempt(s)" in capsys.readouterr().out

    def test_judge_in_host_mode_exits_2(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.brief(1, "cand")
        assert main(["judge", str(h.run), "--round", "1"]) == 2
        assert "no judge command" in capsys.readouterr().err


class TestMcpCommand:
    def test_mcp_without_the_extra_says_how_to_install_it(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setitem(sys.modules, "sealed_panel.mcp_server", None)
        assert main(["mcp"]) == 2
        # While the name is free on the index, never send anyone to it: it could be squatted.
        err = capsys.readouterr().err
        assert "@ git+https://github.com/tdrapp/sealed-panel@v" in err
        assert "pip install 'sealed-panel[mcp]'" not in err


class TestOneJudgePerSeat:
    def test_concurrent_calls_judge_a_seat_once(self, h: Harness) -> None:
        """The judge blocks until the three other calls were turned away, so none of them can
        start after it ended, however slow the machine."""
        release = h.tmp / "release"
        init_runner(h, "--wait-for", str(release))
        h.brief(1, "cand", sealed=True)
        outcomes: list[str] = []

        def go() -> None:
            try:
                outcomes.append("ok" if judge_seat(h.run, 1, 1, True)["ok"] else "rejected")
            except SeatTaken:
                outcomes.append("taken")

        threads = [threading.Thread(target=go) for _ in range(4)]
        try:
            for t in threads:
                t.start()
            deadline = time.monotonic() + 60
            while outcomes.count("taken") < 3 and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            release.touch()
            for t in threads:
                t.join()
        assert sorted(outcomes) == ["ok", "taken", "taken", "taken"]
        rows = results_by_judge(round_dir(h.run, 1, True))[1]
        assert len(rows) == 1 and rows[0]["ok"]
        assert len(list(round_dir(h.run, 1, True).glob("runner_j1_*.log"))) == 1

    def test_a_round_skips_a_seat_another_call_holds(self, h: Harness) -> None:
        init_runner(h)
        h.brief(1, "cand")
        with seat_lock(round_dir(h.run, 1), 1):
            assert [r["judge"] for r in judge_round(h.run, 1, False)] == [2]
            with pytest.raises(SeatTaken, match="seat 1 is held by another call"):
                judge_seat(h.run, 1, 1, False)
            with pytest.raises(EvalError, match=r"held by another call \(\[1\]\)"):
                open_seats(h.run, 1, False)
            with pytest.raises(SeatTaken, match="seat 1 is held by another call"):
                aggregate_round(h.run, 1, False)

    def test_a_seat_filled_after_the_listing_is_skipped(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Another call fills seat 1 between this call's listing and its lock: the round skips
        it (SeatTaken) and goes on, never stops on it."""
        init_runner(h)
        h.brief(1, "cand")
        assert judge_seat(h.run, 1, 1, False)["ok"]
        monkeypatch.setattr(runner, "open_seats", lambda *args: [1, 2])
        assert [r["judge"] for r in judge_round(h.run, 1, False)] == [2]

    def test_a_dead_runner_leaves_a_void_never_a_free_seat(self, h: Harness) -> None:
        """Its started record is there and nobody holds its seat's lock."""
        init_runner(h)
        h.brief(1, "cand")
        held = {"agent_id": "runner-gone", "argv": ["x"], "started_at": time.time()}
        (round_dir(h.run, 1) / "started_j1_runner-gone.json").write_text(json.dumps(held))
        assert open_seats(h.run, 1, False) == [1, 2]
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and void["agent_id"] != held["agent_id"]
        assert "stopped before recording it" in void["errors"][0]
        assert not list(round_dir(h.run, 1).glob("started_j*"))
        assert judge_seat(h.run, 1, 1, False)["ok"]

    def test_an_interrupted_judge_ends_recorded(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        init_runner(h)
        h.brief(1, "cand")

        def interrupted(*args: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(runner, "_launch", interrupted)
        with pytest.raises(KeyboardInterrupt):
            judge_seat(h.run, 1, 1, False)
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and void["errors"] == ["the runner was interrupted (KeyboardInterrupt)"]
        assert not list(round_dir(h.run, 1).glob("started_j*"))


class TestResources:
    def test_output_past_the_cap_is_a_void(self, h: Harness) -> None:
        init_runner(h, "--flood", "8", extra="max_reply_bytes = 1024\n")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"] == ["the judge command wrote more than 1024 bytes"]
        assert len(stored(h, 1)[0]["raw"]) <= 1024

    def test_a_process_left_holding_stdout_cannot_hang_the_runner(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The runner waits up to the grace twice for a stream nobody closes; a shorter grace
        # keeps the test fast and the bound below still holds it to the grace.
        monkeypatch.setattr(runner, "STOP_GRACE_S", 0.5)
        init_runner(h, command=[sys.executable, str(ORPHAN)], extra="timeout_s = 1\n")
        h.brief(1, "cand")
        start = time.monotonic()
        r = judge_seat(h.run, 1, 1, False)
        try:
            assert r["void"] and r["errors"] == ["the judge command timed out after 1 s"]
            # About 2 s here; the grandchild holds stdout for 300 s.
            assert time.monotonic() - start < 1 + 2 * runner.STOP_GRACE_S + 30
        finally:
            log = (round_dir(h.run, 1) / r["runner"]["stderr_log"]).read_text()
            for line in log.splitlines():
                if line.startswith("GRANDCHILD "):
                    with contextlib.suppress(OSError):
                        os.kill(int(line.split()[1]), signal.SIGKILL)

    def test_a_group_that_refuses_the_signal_is_still_stopped(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(pid: int, sig: int) -> None:
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(runner.os, "killpg", refused)
        init_runner(h, "--sleep", "300", extra="timeout_s = 1\n")
        h.brief(1, "cand")
        start = time.monotonic()
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"] == ["the judge command timed out after 1 s"]
        assert time.monotonic() - start < 60  # the judge would have slept 300 s


class TestEnvironmentHashes:
    def test_attempts_under_one_value_are_not_flagged(self) -> None:
        a = {"runner": {"env_sha256": {"KEY": "1", "MODEL": "m"}}}
        host = {"source": "transcript"}  # no runner proof: left out
        assert env_changes({1: [a, host], 2: [dict(a)]}) == []

    def test_a_changed_or_dropped_value_is_flagged(self) -> None:
        a = {"runner": {"env_sha256": {"KEY": "1", "MODEL": "m"}}}
        b = {"runner": {"env_sha256": {"KEY": "2"}}}
        assert env_changes({1: [a], 2: [b]}) == ["KEY", "MODEL"]

    def test_the_proof_holds_a_hash_per_allowed_value(self) -> None:
        cfg = Config(merge(default_data(), {"judge": {"env_allow": ["KEY", "UNSET"]}}))
        hashes = env_hashes(cfg, {"KEY": "secret", "PATH": "/bin"})
        assert hashes == {"KEY": hashlib.sha256(b"secret").hexdigest()}
