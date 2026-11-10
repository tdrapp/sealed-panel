"""How a run touches the disk, and what a hostile judge can make it do: whole owner-only
writes, no link written through, one judge per seat under its lock, bounded output, leftovers of
a killed runner, replies built to break the reader."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel import brief as brief_module
from sealed_panel import cli, library, record, runner, store
from sealed_panel.aggregate import aggregate_round
from sealed_panel.config import (
    Config,
    ConfigError,
    config_differences,
    default_config,
    default_data,
    load_config,
    merge,
)
from sealed_panel.errors import EvalError, SeatTaken
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import extract_json, record_from, record_reply, seat_lock
from sealed_panel.report import render_report
from sealed_panel.runner import judge_round, judge_seat, open_seats
from sealed_panel.spec import init_run, run_config, run_library
from sealed_panel.store import contained, dump_json, locked, round_dir, write_file

STUB = Path(__file__).resolve().parent / "stub_judge.py"
SRC = Path(__file__).resolve().parent.parent / "src" / "sealed_panel"


def init_judge(h: Harness, argv: list[str], extra: str = "", **spec: Any) -> None:
    cfg = h.tmp / "judge.toml"
    cfg.write_text(f"[judge]\ncommand = {json.dumps(argv)}\n{extra}")
    sp = h.tmp / "spec.json"
    sp.write_text(json.dumps(make_spec(**{"judges_per_persona": 2, "max_rounds": 1, **spec})))
    init_run(h.run, sp, load_config(cfg))


class TestWrites:
    def test_a_link_on_the_target_is_replaced_never_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.txt"
        outside.write_text("ORIGINAL")
        target = tmp_path / "file.md"
        target.symlink_to(outside)
        write_file(target, "new")
        assert outside.read_text() == "ORIGINAL"
        assert not target.is_symlink() and target.read_text() == "new"

    @pytest.mark.parametrize("existing", [None, 0o644], ids=["new", "replaced"])
    def test_every_file_is_written_owner_only(self, tmp_path: Path, existing: int | None) -> None:
        path = tmp_path / "f.json"
        if existing is not None:
            path.write_text("{}")
            os.chmod(path, existing)
        old = os.umask(0)
        try:
            dump_json(path, {"a": 1})
        finally:
            os.umask(old)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert json.loads(path.read_text()) == {"a": 1}

    def test_nothing_is_left_behind(self, tmp_path: Path) -> None:
        folder = tmp_path / "here"
        folder.mkdir()
        write_file(folder / "a.md", "x")
        (folder / "adir").mkdir()
        with pytest.raises(OSError):
            dump_json(folder / "adir", {"a": 1})
        assert sorted(p.name for p in folder.iterdir()) == ["a.md", "adir"]

    @pytest.mark.parametrize("name", ["brief.py", "report.py", "spec.py", "record.py"])
    def test_a_run_is_written_through_the_one_helper(self, name: str) -> None:
        if not SRC.is_dir():
            pytest.skip("the package is not here")
        assert ".write_text(" not in (SRC / name).read_text(encoding="utf-8")

    @pytest.mark.parametrize("planted", ["candidate.md", "keys.json"])
    def test_a_link_planted_on_a_round_file_is_not_followed(self, h: Harness, planted: str) -> None:
        """A link on a file the brief writes is replaced, never written through."""
        h.init(judges_per_persona=2)
        outside = h.tmp / "outside.txt"
        outside.write_text("ORIGINAL")
        rdir = h.run / "rounds" / "r1"
        rdir.mkdir(parents=True)
        (rdir / planted).symlink_to(outside)
        h.brief(1, "cand")
        assert outside.read_text() == "ORIGINAL"
        assert not (rdir / planted).is_symlink()

    def test_a_planted_findings_link_is_replaced_never_followed(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        outside = h.tmp / "outside.txt"
        outside.write_text("ORIGINAL")
        (h.run / "feedback").mkdir()
        (h.run / "feedback" / "after_r1.md").symlink_to(outside)
        h.full(1, "cand", FLAT)
        assert outside.read_text() == "ORIGINAL"
        assert "Findings" in (h.run / "feedback" / "after_r1.md").read_text()


class TestLibraryLinks:
    def outside(self, tmp_path: Path) -> Path:
        folder = tmp_path / "outside"
        folder.mkdir()
        return folder

    @pytest.mark.parametrize("name", ["inbox", "history"])
    def test_a_linked_folder_is_refused(self, tmp_path: Path, name: str) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        (lib / name).symlink_to(self.outside(tmp_path))
        with pytest.raises(EvalError, match="symbolic link"):
            (library.inbox_dir if name == "inbox" else library.history_dir)(lib)

    @pytest.mark.slow
    def test_no_candidate_is_written_through_a_linked_inbox(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--score", "5"], max_rounds=1)
        h.brief(1, "cand", sealed=True)
        judge_round(h.run, 1, True)
        aggregate_round(h.run, 1, True)
        lib = h.tmp / "lib"
        lib.mkdir()
        outside = self.outside(h.tmp)
        (lib / "inbox").symlink_to(outside)
        with pytest.raises(EvalError, match="symbolic link"):
            cli.user_disagreement(h.run, "the user reads this differently", None, lib)
        assert list(outside.iterdir()) == []

    def test_a_linked_template_is_not_served(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        lib.mkdir()
        template = {**library.builtin_templates()["message"], "id": "outside-tpl"}
        outside = self.outside(tmp_path) / "t.json"
        outside.write_text(json.dumps(template))
        (lib / "outside-tpl.json").symlink_to(outside)
        found, warnings = library.user_templates(lib)
        assert "outside-tpl" not in found
        assert any("symbolic link" in w for w in warnings)

    def test_a_linked_candidate_is_not_read(self, tmp_path: Path) -> None:
        lib = tmp_path / "lib"
        (lib / "inbox").mkdir(parents=True)
        outside = self.outside(tmp_path) / "c.json"
        outside.write_text(json.dumps({"kind": "user_disagreement", "status": "open"}))
        (lib / "inbox" / "c-0123456789ab.json").symlink_to(outside)
        rows, warnings = library.open_candidates(lib)
        assert rows == [] and any("symbolic link" in w for w in warnings)
        with pytest.raises(EvalError, match="symbolic link"):
            library._open_candidate(lib, "c-0123456789ab")


class TestRunnerStreams:
    @pytest.mark.slow
    def test_stderr_past_its_cap_is_a_void(self, h: Harness) -> None:
        argv = [sys.executable, str(STUB), "--flood-stderr", "8"]
        init_judge(h, argv, extra="max_stderr_bytes = 1024\n")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"] == [
            "the judge command wrote more than 1024 bytes to stderr"
        ]
        assert (round_dir(h.run, 1) / r["runner"]["stderr_log"]).stat().st_size <= 1024

    @pytest.mark.slow
    def test_stderr_under_its_cap_is_kept_whole(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--noise"])
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert (
            r["ok"]
            and "stub noise" in (round_dir(h.run, 1) / r["runner"]["stderr_log"]).read_text()
        )

    @pytest.mark.parametrize("value", [1023, (16 << 20) + 1])
    def test_the_stderr_cap_is_bounded(self, value: int) -> None:
        with pytest.raises(ConfigError, match=r"judge\.max_stderr_bytes"):
            Config(merge(default_data(), {"judge": {"max_stderr_bytes": value}}))

    @pytest.mark.slow
    def test_an_interruption_stops_the_judge_and_records_it(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        init_judge(h, [sys.executable, str(STUB), "--sleep", "300"])
        h.brief(1, "cand")

        def interrupted(seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(runner.time, "sleep", interrupted)
        start = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            judge_seat(h.run, 1, 1, False)
        assert time.monotonic() - start < 60  # the judge would have slept 300 s
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "interrupted" in void["errors"][0]
        assert not list(round_dir(h.run, 1).glob("started_j*"))


def started(h: Harness, judge: int, held: dict[str, Any], name: str = "runner-dead") -> Path:
    """The record a runner writes before its process starts, left by a runner that died."""
    path = round_dir(h.run, 1) / f"started_j{judge}_{name}.json"
    path.write_text(json.dumps(held))
    return path


class TestLeftovers:
    def test_a_started_record_becomes_a_void_at_open_seats(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        path = started(h, 1, {"agent_id": "runner-dead", "argv": ["x"], "started_at": 0})
        assert open_seats(h.run, 1, False) == [1, 2]
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and void["agent_id"].startswith("runner-")
        assert void["agent_id"] != "runner-dead"  # never an id the record names
        assert not path.exists()

    @pytest.mark.slow
    def test_a_started_record_becomes_a_void_at_aggregate(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        judge_round(h.run, 1, False, [2])
        path = started(h, 1, {"agent_id": "runner-dead", "argv": ["x"], "started_at": 0})
        with pytest.raises(EvalError, match="never fills a seat"):
            aggregate_round(h.run, 1, False)
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "stopped before recording it" in void["errors"][0]
        assert not path.exists()

    @pytest.mark.slow
    def test_a_started_record_on_a_filled_seat_gives_no_void(self, h: Harness) -> None:
        """A runner that recorded its reply and died before removing its record: a void there
        would veto a reply already seen."""
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        judge_round(h.run, 1, False)
        path = started(h, 1, {"argv": ["x"], "started_at": 0})
        aggregate_round(h.run, 1, False)
        assert [r["ok"] for r in results_by_judge(round_dir(h.run, 1))[1]] == [True]
        assert not path.exists()

    @pytest.mark.slow
    def test_a_started_record_with_a_used_agent_still_gives_a_void(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        used = judge_seat(h.run, 1, 1, False)["agent_id"]
        started(h, 2, {"agent_id": used, "argv": ["x"], "started_at": 0})
        assert open_seats(h.run, 1, False) == [2]
        [void] = results_by_judge(round_dir(h.run, 1))[2]
        assert void["void"] and void["agent_id"] != used

    def test_an_unreadable_started_record_still_gives_a_void(self, h: Harness) -> None:
        """A power loss right after the write can leave it empty: its runner died all the
        same."""
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        for name, text in (("runner-cut", ""), ("runner-list", "[1]")):
            (round_dir(h.run, 1) / f"started_j1_{name}.json").write_text(text)
        assert open_seats(h.run, 1, False) == [1, 2]
        voids = results_by_judge(round_dir(h.run, 1))[1]
        assert len(voids) == 2 and all(v["void"] for v in voids)
        assert all(set(v["runner"]) == {"stale", "failure"} for v in voids)
        assert not list(round_dir(h.run, 1).glob("started_j*"))

    @pytest.mark.slow
    def test_a_started_record_of_a_stored_attempt_gives_no_second_void(self, h: Harness) -> None:
        """The runner stored its attempt (a void here) and died before removing the record."""
        init_judge(h, [sys.executable, str(STUB), "--exit", "1"])
        h.brief(1, "cand")
        void = judge_seat(h.run, 1, 1, False)
        path = started(h, 1, {"agent_id": void["agent_id"], "argv": ["x"], "started_at": 0})
        open_seats(h.run, 1, False)
        assert [r["agent_id"] for r in results_by_judge(round_dir(h.run, 1))[1]] == [
            void["agent_id"]
        ]
        assert not path.exists()

    @pytest.mark.slow
    def test_a_started_attempt_counts_against_the_void_cap(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--exit", "1"])
        h.brief(1, "cand")
        for _ in range(3):
            judge_seat(h.run, 1, 1, False)
        path = started(h, 1, {"agent_id": "runner-last", "argv": ["x"], "started_at": 0})
        with pytest.raises(EvalError, match="voided attempts"):
            open_seats(h.run, 1, False)
        assert len(results_by_judge(round_dir(h.run, 1))[1]) == 3
        assert not path.exists()


class TestSeatLock:
    """One call at a time on a seat: an flock held from the seat check to the stored attempt."""

    def reply(self, h: Harness, judge: int) -> str:
        meta = next(m for m in h.keys(1)["judges"] if m["judge"] == judge)
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        return judge_json(meta, h.texts(1, meta), sc)

    def test_a_held_lock_refuses_a_second_holder_until_released(self, tmp_path: Path) -> None:
        path = tmp_path / "seat.lock"
        with locked(path, "busy"), pytest.raises(SeatTaken, match="busy"), locked(path, "busy"):
            pass
        with locked(path, "busy"):
            pass

    def test_host_record_on_a_held_seat_is_refused(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        held = seat_lock(round_dir(h.run, 1), 1)
        with held, pytest.raises(SeatTaken, match="seat 1 is held by another call"):
            record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)
        assert results_by_judge(round_dir(h.run, 1)) == {}
        assert record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)["ok"]

    def test_two_host_records_on_one_seat_give_one_reply(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first call stops inside the seat check; the second meets the lock, never the
        check, so both can never pass it."""
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        entered, go = threading.Event(), threading.Event()
        real = record.check_seat

        def slow_first(*args: Any) -> None:
            if not entered.is_set():
                entered.set()
                go.wait(30)
            real(*args)

        monkeypatch.setattr(record, "check_seat", slow_first)
        first: list[dict[str, Any]] = []
        thread = threading.Thread(
            target=lambda: first.append(record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False))
        )
        thread.start()
        try:
            assert entered.wait(30)
            with pytest.raises(SeatTaken):
                record_reply(h.run, 1, 1, "a2", self.reply(h, 1) + " ", False)
        finally:
            go.set()
            thread.join(30)
        assert first[0]["ok"]
        assert [r["agent_id"] for r in results_by_judge(round_dir(h.run, 1))[1]] == ["a1"]

    def test_aggregate_refuses_while_a_seat_is_held(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        for j in (1, 2):
            record_reply(h.run, 1, j, f"a{j}", self.reply(h, j), False)
        with seat_lock(round_dir(h.run, 1), 2), pytest.raises(SeatTaken, match="seat 2 is held"):
            aggregate_round(h.run, 1, False)
        assert not (round_dir(h.run, 1) / "result.json").exists()
        aggregate_round(h.run, 1, False)

    def test_a_round_aggregated_meanwhile_is_not_judged(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The round is aggregated between the runner's first check and its lock."""
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        real = runner.seat_lock

        @contextlib.contextmanager
        def closed_meanwhile(rdir: Path, judge: int) -> Iterator[None]:
            (rdir / "result.json").write_text("{}")
            with real(rdir, judge):
                yield

        monkeypatch.setattr(runner, "seat_lock", closed_meanwhile)
        with pytest.raises(EvalError, match="already aggregated"):
            judge_seat(h.run, 1, 1, False)
        assert not list(round_dir(h.run, 1).glob("started_j*"))
        assert results_by_judge(round_dir(h.run, 1)) == {}

    def test_a_filesystem_without_flock_is_refused(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")

        def unsupported(fd: int, op: int) -> None:
            raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))

        monkeypatch.setattr(store.fcntl, "flock", unsupported)
        with pytest.raises(EvalError, match="does not support file locks"):
            record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)
        assert results_by_judge(round_dir(h.run, 1)) == {}

    def test_a_filesystem_without_flock_starts_no_judge(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")

        def unsupported(fd: int, op: int) -> None:
            raise OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))

        monkeypatch.setattr(store.fcntl, "flock", unsupported)
        with pytest.raises(EvalError, match="does not support file locks"):
            judge_seat(h.run, 1, 1, False)
        assert not list(round_dir(h.run, 1).glob("started_j*"))
        assert results_by_judge(round_dir(h.run, 1)) == {}

    @pytest.mark.slow
    def test_a_dead_holder_releases_the_seat(self, h: Harness) -> None:
        """The kernel drops an flock with its process: no age or liveness test is needed."""
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        lock = round_dir(h.run, 1) / "seat_j1.lock"
        code = (
            "import fcntl, os, sys, time; "
            f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT); "
            "fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); time.sleep(600)"
        )
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            assert proc.stdout is not None and proc.stdout.readline().strip() == "held"
            with pytest.raises(SeatTaken):
                record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)
        finally:
            proc.kill()
            proc.wait()
        assert record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)["ok"]


class TestRunLock:
    """What spans the whole run (agent ids and replies used, a round's briefs, the aggregate)
    is read and written under the run's lock, taken after any seat lock."""

    def reply(self, h: Harness, judge: int) -> str:
        meta = next(m for m in h.keys(1)["judges"] if m["judge"] == judge)
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        return judge_json(meta, h.texts(1, meta), sc)

    def test_one_agent_on_two_seats_at_once_is_recorded_once(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first call stops inside its seat check, run lock held; the second, on another
        seat with the same agent id, waits for the lock and then meets the recorded agent."""
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        entered, second_in, go = threading.Event(), threading.Event(), threading.Event()
        real = record.check_seat

        def slow_first(*args: Any) -> None:
            if not entered.is_set():
                entered.set()
                go.wait(30)
            else:
                second_in.set()  # only reachable without the run lock
            real(*args)

        monkeypatch.setattr(record, "check_seat", slow_first)
        outcomes: list[Any] = []

        def second() -> None:
            try:
                outcomes.append(record_reply(h.run, 1, 2, "SAME", self.reply(h, 2), False))
            except EvalError as exc:
                outcomes.append(exc)

        raw = self.reply(h, 1)
        first = threading.Thread(
            target=lambda: outcomes.append(record_reply(h.run, 1, 1, "SAME", raw, False))
        )
        first.start()
        assert entered.wait(30)
        other = threading.Thread(target=second)
        other.start()
        assert not second_in.wait(2), "the second call passed the run-wide check meanwhile"
        go.set()
        first.join(30)
        other.join(30)
        assert sum(isinstance(o, dict) and o["ok"] for o in outcomes) == 1
        assert any(isinstance(o, EvalError) and "already judged" in str(o) for o in outcomes)
        rows = results_by_judge(round_dir(h.run, 1)).values()
        assert [r["agent_id"] for rs in rows for r in rs] == ["SAME"]

    def test_two_briefings_of_one_round_give_one(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first briefing stops before its first brief is written, run lock held; the second
        waits for the lock and then meets the briefs."""
        h.init(judges_per_persona=2)
        entered, second_in, go = threading.Event(), threading.Event(), threading.Event()
        real = brief_module.build_brief

        def slow_first(*args: Any) -> str:
            if not entered.is_set():
                entered.set()
                go.wait(30)
            elif threading.current_thread() is not first:
                second_in.set()  # only reachable without the run lock
            return real(*args)

        monkeypatch.setattr(brief_module, "build_brief", slow_first)
        outcomes: list[Any] = []

        def seal(name: str) -> None:
            try:
                outcomes.append(h.brief(1, name))
            except EvalError as exc:
                outcomes.append(exc)

        first = threading.Thread(target=seal, args=("cand",))
        first.start()
        assert entered.wait(30)
        other = threading.Thread(target=seal, args=("cand",))
        other.start()
        assert not second_in.wait(2), "the second briefing passed the checks meanwhile"
        go.set()
        first.join(30)
        other.join(30)
        assert sum(isinstance(o, list) for o in outcomes) == 1
        assert any(isinstance(o, EvalError) for o in outcomes)

    def test_a_run_inside_a_run_is_refused(self, h: Harness) -> None:
        """Through MCP the author names the run path: a run created in a round folder would
        put a directory where the round's lock or result goes."""
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        for inner in ("seat_j1.lock", "result.json", "x"):
            with pytest.raises(EvalError, match="inside the run"):
                init_run(round_dir(h.run, 1) / inner, sp)
        assert sorted(p.name for p in round_dir(h.run, 1).iterdir() if p.is_dir()) == []
        assert record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)["ok"]

    def test_a_lock_path_taken_by_a_directory_is_a_clear_refusal(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        (round_dir(h.run, 1) / "seat_j1.lock").mkdir()
        with pytest.raises(EvalError, match="cannot open the lock file"):
            record_reply(h.run, 1, 1, "a1", self.reply(h, 1), False)

    @pytest.mark.slow
    def test_the_judge_log_is_owner_only(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--noise"])
        h.brief(1, "cand")
        old = os.umask(0)
        try:
            r = judge_seat(h.run, 1, 1, False)
        finally:
            os.umask(old)
        log = round_dir(h.run, 1) / r["runner"]["stderr_log"]
        assert stat.S_IMODE(log.stat().st_mode) == 0o600


class TestContainment:
    def test_a_path_inside_the_root_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        assert contained(tmp_path / "a" / ".." / "f.md", tmp_path) == tmp_path.resolve() / "f.md"

    @pytest.mark.parametrize("how", ["dotdot", "link"])
    def test_a_path_leaving_the_root_is_refused(self, tmp_path: Path, how: str) -> None:
        root = tmp_path / "root"
        root.mkdir()
        (tmp_path / "outside").mkdir()
        (root / "out").symlink_to(tmp_path / "outside")
        given = root / ".." / "outside" / "f.md" if how == "dotdot" else root / "out" / "f.md"
        with pytest.raises(EvalError, match="outside the root"):
            contained(given, root)

    def test_the_refusal_names_the_path_as_given(self, h: Harness) -> None:
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps({**make_spec(), "facts_file": "../elsewhere.md"}))
        refused = r"^facts_file '\.\./elsewhere\.md' is outside the root"
        with pytest.raises(EvalError, match=refused):
            init_run(h.run, sp, None, None, h.tmp)


class TestHostileReplies:
    def judge_script(self, h: Harness, code: str) -> None:
        init_judge(h, [sys.executable, "-c", f"import sys;sys.stdin.read();{code}"])
        h.brief(1, "cand")

    @pytest.mark.parametrize(
        "code",
        [
            "sys.stdout.write('{\"a\":'+'['*200000)",
            "sys.stdout.write('{\"a\":'+'['*200000+']'*200000+'}')",
            "sys.stdout.write('{\"a\":'+'1'*5000+'}')",
        ],
        ids=["open", "closed", "bigint"],
    )
    @pytest.mark.slow
    def test_a_reply_built_to_break_the_parser_is_rejected(self, h: Harness, code: str) -> None:
        self.judge_script(h, code)
        r = judge_seat(h.run, 1, 1, False)
        assert not r["ok"] and not r.get("void") and r["errors"][0].startswith("unparseable")
        assert not list(round_dir(h.run, 1).glob("started_j*"))

    def test_a_reply_too_deep_to_store_is_rejected_not_lost(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Parsed, but too deep for the JSON writer: how deep depends on the Python version."""
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        raw = judge_json(meta, h.texts(1, meta), sc)
        real = record._store
        calls = []

        def deep_once(*args: Any) -> None:
            calls.append(args)
            if len(calls) == 1:
                raise RecursionError
            real(*args)

        monkeypatch.setattr(record, "_store", deep_once)
        r = record_reply(h.run, 1, 1, "deep", raw, False)
        assert not r["ok"] and r["errors"] == ["unparseable: nested too deeply"]
        assert len(results_by_judge(round_dir(h.run, 1))[1]) == 1

    def test_odd_types_never_break_the_round(self, h: Harness) -> None:
        h.init(judges_per_persona=2, max_rounds=1)
        h.brief(1, "cand", sealed=True)
        for meta in h.keys(1, True)["judges"]:
            sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
            raw = judge_json(meta, h.texts(1, meta, True), sc)
            reply = json.loads(raw.split("```json\n")[1][:-4])
            for item in reply["items"].values():
                item["retell"] = {"x": [1]}
                item["weakest_point"] = {"text": {"a": 1}, "dimension": ["clarity"], "quote": 7}
                for cell in item["dimensions"].values():
                    cell["reasoning"] = ["nested", {"a": 1}]
            record_reply(h.run, 1, meta["judge"], f"odd{meta['judge']}", json.dumps(reply), True)
        aggregate_round(h.run, 1, True)
        assert "Grade (sealed panel)" in render_report(h.run)

    def test_a_reply_that_made_the_regex_quadratic_is_read_at_once(self) -> None:
        """1 MiB took 72 s with the old pattern, and it grows with the square of the size. At
        8 MiB that would be over an hour; the bound leaves room for a slow machine."""
        start = time.monotonic()
        with pytest.raises((ValueError, EvalError)):
            extract_json("```{" * (1 << 21))
        assert time.monotonic() - start < 30

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('Note {x}: ```json\n{"a": 1}\n``` and more', {"a": 1}),
            ('```\n{"a": 2}\n```', {"a": 2}),
            ('plain {"a": 3} text', {"a": 3}),
            ('{"a": "``` not a fence ```"}', {"a": "``` not a fence ```"}),
            ('```json\n{"a": 4}\n```\nthen ```another``` {tail', {"a": 4}),
        ],
    )
    def test_the_reply_is_found(self, text: str, expected: Any) -> None:
        assert extract_json(text) == expected

    def test_no_object_at_all_is_refused(self) -> None:
        with pytest.raises(EvalError, match="no JSON object"):
            extract_json("nothing here")


class TestToolBlockTypes:
    @pytest.mark.parametrize("block_type", ["server_tool_use", "mcp_tool_use"])
    def test_every_kind_of_tool_call_counts(self, h: Harness, block_type: str) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        raw = judge_json(meta, h.texts(1, meta), sc)
        brief = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        block = {"type": block_type, "id": "t1", "name": "web_fetch", "input": {}}
        rows = [
            {"type": "user", "agentId": "t", "message": {"content": brief}},
            {"type": "assistant", "agentId": "t", "message": {"content": [block]}},
            {"type": "assistant", "agentId": "t",
             "message": {"content": [{"type": "text", "text": raw}]}},
        ]  # fmt: skip
        folder = h.tmp / "tasks"
        folder.mkdir()
        t = folder / "t.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in rows))
        r = record_from(h.run, 1, 1, "t", False, t)
        assert not r["ok"] and "(web_fetch)" in r["errors"][0]


class TestCommandLineEdges:
    def test_ctrl_c_exits_with_a_message_not_a_traceback(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")

        def interrupted(*args: Any) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "judge_seat", interrupted)
        assert cli.main(["judge", str(h.run), "--round", "1"]) == cli.INTERRUPTED
        assert "interrupted" in capsys.readouterr().out

    def test_a_seat_taken_meanwhile_is_skipped(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")

        def taken(run: Path, rnd: int, seat: int, sealed: bool) -> dict[str, Any]:
            raise SeatTaken(f"judge {seat} is being judged by another call")

        monkeypatch.setattr(cli, "judge_seat", taken)
        assert cli.main(["judge", str(h.run), "--round", "1"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("SKIPPED judge 1: judge 1 is being judged")

    @pytest.mark.slow
    def test_a_killed_runner_leaves_a_void_for_the_next_call(self, h: Harness) -> None:
        """The judge blocks until killed, so the runner is always killed mid-judge, however slow
        the machine. The judge runs in a session of its own, out of reach of the runner's death:
        the test stops it, and the runner's temporary folder stays under the test's."""
        pid_file = h.tmp / "judge.pid"
        init_judge(h, [sys.executable, str(STUB), "--sleep", "600", "--pid-file", str(pid_file)])
        h.brief(1, "cand")
        code = (
            "import sys; from pathlib import Path; from sealed_panel.runner import judge_seat; "
            f"judge_seat(Path({str(h.run)!r}), 1, 1, False)"
        )
        (h.tmp / "tmp").mkdir()
        env = {**os.environ, "PYTHONPATH": str(SRC.parent), "TMPDIR": str(h.tmp / "tmp")}
        proc = subprocess.Popen([sys.executable, "-c", code], env=env)
        judge = 0
        try:
            deadline = time.monotonic() + 60
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            judge = int(pid_file.read_text())
            with pytest.raises(EvalError, match="seat is held by another call"):
                open_seats(h.run, 1, False, [1])  # held for as long as the judge runs
        finally:
            proc.kill()
            proc.wait()
            if judge:
                stop_group(judge)
        [left] = round_dir(h.run, 1).glob("started_j1_*.json")
        assert open_seats(h.run, 1, False) == [1, 2]  # the kernel released the dead runner's lock
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "stopped before recording it" in void["errors"][0]
        assert not left.exists()


def stop_group(pid: int) -> None:
    """Kill a process group and wait until it is gone (reaped by init once its parent died)."""
    with contextlib.suppress(OSError):
        os.killpg(pid, signal.SIGKILL)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:  # macOS: every member exited, not yet reaped
            pass
        time.sleep(0.05)
    raise AssertionError(f"the judge group {pid} outlived the test")


def relock(run: Path, data: dict[str, Any]) -> None:
    """What someone editing the run's files by hand does: the config and its lock together."""
    (run / "config.json").write_text(json.dumps(data))
    (run / "config.lock").write_text(Config(data).fingerprint + "\n")


class TestFrozenConfiguration:
    def test_a_dotted_key_is_refused_as_unknown(self, h: Harness) -> None:
        """No default key holds a dot, so a key that would shadow another in a flattened view
        is an unknown key."""
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        data["transcript"]["tool_block_type"] = "nothing"
        data["transcript.tool_block_type"] = "tool_use"
        relock(h.run, data)
        with pytest.raises(EvalError, match=r"unknown configuration key 'transcript\.tool_block"):
            run_config(h.run)

    def test_an_unknown_key_is_refused(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        data["limits"]["extra"] = 1
        relock(h.run, data)
        with pytest.raises(EvalError, match=r"unknown configuration key 'limits\.extra'"):
            run_config(h.run)

    def test_punctuation_keeps_its_open_table(self, h: Harness) -> None:
        cfg = h.tmp / "p.toml"
        cfg.write_text('[text.punctuation]\n"\\u2032" = "\'"\n')
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg))
        assert run_config(h.run).punctuation[chr(0x2032)] == "'"

    def test_differences_are_read_section_by_section(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        data["limits"]["max_replacements"] = 5
        data["judge"]["timeout_s"] = 9
        relock(h.run, data)
        assert config_differences(run_config(h.run), default_config()) == [
            "judge.timeout_s",
            "limits.max_replacements",
        ]

    def test_a_key_an_older_run_lacks_counts_as_its_default(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        del data["judge"]["max_stderr_bytes"]
        del data["mcp"]
        relock(h.run, data)
        assert config_differences(run_config(h.run), default_config()) == []

    def test_a_present_old_value_still_differs(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        data["transcript"]["handback_tool"] = ""
        relock(h.run, data)
        assert config_differences(run_config(h.run), default_config()) == [
            "transcript.handback_tool"
        ]


class TestStartedRecords:
    def run_mode(self, h: Harness, *args: str) -> Path:
        init_judge(h, [sys.executable, str(STUB), *args])
        h.brief(1, "cand")
        return round_dir(h.run, 1)

    def test_started_records_never_pass_the_void_cap(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        for i in range(5):
            started(h, 1, {}, f"runner-m{i}")
        with pytest.raises(EvalError, match="voided attempts"):
            open_seats(h.run, 1, False)
        assert len(results_by_judge(rdir)[1]) == 3

    @pytest.mark.slow
    def test_a_seat_at_its_cap_never_fails_the_round(self, h: Harness) -> None:
        """Aggregate settles every seat: past the cap, a record is dropped, and the round is
        refused for what it lacks, never for the cap."""
        rdir = self.run_mode(h)
        assert open_seats(h.run, 1, False, [2]) == [2]
        judge_seat(h.run, 1, 2, False)
        for i in range(5):
            started(h, 1, {}, f"runner-m{i}")
        with pytest.raises(EvalError, match="never fills a seat"):
            aggregate_round(h.run, 1, False)
        assert len(results_by_judge(rdir)[1]) == 3
        assert not list(rdir.glob("started_j*"))

    def test_a_void_keeps_nothing_but_the_command_and_its_start(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        held = {"started_at": 0, "argv": ["x"], "agent_id": "author-pick", "note": "CANARY"}
        started(h, 1, held)
        open_seats(h.run, 1, False)
        [void] = results_by_judge(rdir)[1]
        assert void["agent_id"].startswith("runner-") and "author-pick" not in json.dumps(void)
        assert set(void["runner"]) == {"started_at", "argv", "stale", "failure"}

    def test_started_records_mean_nothing_in_host_mode(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        rdir = round_dir(h.run, 1)
        started(h, 1, {"agent_id": "A1"})
        meta = h.keys(1)["judges"][1]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        record_reply(h.run, 1, 2, "a2", judge_json(meta, h.texts(1, meta), sc), False)
        with pytest.raises(EvalError, match="no real attempt"):
            aggregate_round(h.run, 1, False)
        assert 1 not in results_by_judge(rdir)  # seat 1 empty: the record voided nothing


class TestLibraryMoved:
    def test_a_library_replaced_by_a_link_is_refused(self, h: Harness) -> None:
        lib = h.tmp / "lib"
        lib.mkdir()
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, None, lib)
        outside = h.tmp / "outside"
        outside.mkdir()
        lib.rename(h.tmp / "lib.real")
        lib.symlink_to(outside)
        with pytest.raises(EvalError, match="a link was put in its place"):
            run_library(h.run)
        assert list(outside.iterdir()) == []

    def test_a_linked_library_folder_writes_nothing(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        lib = tmp_path / "lib"
        lib.symlink_to(outside)
        for folder in (library.inbox_dir, library.history_dir):
            with pytest.raises(EvalError, match="symbolic link"):
                folder(lib)


class TestHandbackType:
    def test_the_handback_is_exempt_only_as_a_plain_tool_call(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        raw = judge_json(meta, h.texts(1, meta), sc)
        brief = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        odd = {"type": "server_tool_use", "id": "t", "name": "SubagentHandback",
               "input": {"message": "x"}}  # fmt: skip
        rows = [
            {"type": "user", "agentId": "o", "message": {"content": brief}},
            {"type": "assistant", "agentId": "o",
             "message": {"content": [odd, {"type": "text", "text": raw}]}},
        ]  # fmt: skip
        folder = h.tmp / "tasks"
        folder.mkdir()
        t = folder / "o.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in rows))
        r = record_from(h.run, 1, 1, "o", False, t)
        assert not r["ok"] and "(SubagentHandback)" in r["errors"][0]


class TestCommandLineArguments:
    def test_an_empty_transcript_root_argument_is_accepted(self) -> None:
        args = cli._parser().parse_args(["mcp", "--transcript-root"])
        assert args.transcript_root == [""]
