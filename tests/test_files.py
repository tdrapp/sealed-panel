"""How a run touches the disk, and what a hostile judge can make it do: whole writes, no link
followed, bounded output, leftovers of a killed runner, replies built to break the reader."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
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
from sealed_panel.errors import EvalError
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import extract_json, record_from, record_reply
from sealed_panel.report import render_report
from sealed_panel.runner import SeatTaken, judge_round, judge_seat, open_seats
from sealed_panel.spec import init_run, run_config, run_library
from sealed_panel.store import dump_json, round_dir, write_file

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

    def test_a_link_on_the_directory_stops_the_write(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        link = tmp_path / "dir"
        link.symlink_to(outside)
        with pytest.raises(EvalError, match="symbolic link"):
            write_file(link / "file.md", "new")
        assert list(outside.iterdir()) == []

    def test_an_existing_file_keeps_its_mode(self, tmp_path: Path) -> None:
        path = tmp_path / "keep.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        dump_json(path, {"a": 1})
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert json.loads(path.read_text()) == {"a": 1}

    def test_a_new_file_follows_the_umask(self, tmp_path: Path) -> None:
        old = os.umask(0o027)
        try:
            write_file(tmp_path / "new.md", "x")
        finally:
            os.umask(old)
        assert stat.S_IMODE((tmp_path / "new.md").stat().st_mode) == 0o640

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
    def test_a_link_planted_after_the_check_is_not_followed(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, planted: str
    ) -> None:
        """The race the probe won in a few tries: the check passes, then the link appears."""
        monkeypatch.setattr(brief_module, "refuse_links", lambda *paths: None)
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
    def test_a_linked_brief_never_reaches_the_judge(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--show-env"])
        h.brief(1, "cand")
        outside = h.tmp / "secret.txt"
        outside.write_text("OUTSIDE-SECRET\n")
        brief = round_dir(h.run, 1) / "brief_j1.md"
        brief.unlink()
        brief.symlink_to(outside)
        with pytest.raises(EvalError, match="symbolic link"):
            judge_seat(h.run, 1, 1, False)
        assert not list(round_dir(h.run, 1).glob("runner_j*.log"))
        assert not list(round_dir(h.run, 1).glob("running_j*"))

    def test_stderr_past_its_cap_is_a_void(self, h: Harness) -> None:
        argv = [sys.executable, str(STUB), "--flood-stderr", "8"]
        init_judge(h, argv, extra="max_stderr_bytes = 1024\n")
        h.brief(1, "cand")
        r = judge_seat(h.run, 1, 1, False)
        assert r["void"] and r["errors"] == [
            "the judge command wrote more than 1024 bytes to stderr"
        ]
        assert (round_dir(h.run, 1) / r["runner"]["stderr_log"]).stat().st_size <= 1024

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
        assert not list(round_dir(h.run, 1).glob("running_j*"))


class TestLeftovers:
    def stale(self, h: Harness, held: dict[str, Any]) -> Path:
        path = round_dir(h.run, 1) / "running_j1.json.abc.stale"
        path.write_text(json.dumps(held))
        return path

    def test_a_marker_moved_aside_becomes_a_void_at_open_seats(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        path = self.stale(h, {"pid": 0, "agent_id": "runner-dead", "started": 0})
        assert open_seats(h.run, 1, False) == [1, 2]
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and void["agent_id"].startswith("runner-")
        assert void["agent_id"] != "runner-dead"  # never an id the marker names
        assert not path.exists()

    def test_a_marker_moved_aside_becomes_a_void_at_aggregate(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        judge_round(h.run, 1, False, [2])
        path = self.stale(h, {"pid": 0, "agent_id": "runner-dead", "started": 0})
        with pytest.raises(EvalError, match="never fills a seat"):
            aggregate_round(h.run, 1, False)
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "stopped before recording it" in void["errors"][0]
        assert not path.exists()

    def test_a_marker_on_a_filled_seat_gives_no_void(self, h: Harness) -> None:
        """A runner never starts on a filled seat, so the marker is planted: a void there would
        let the author veto a reply it has seen."""
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        judge_round(h.run, 1, False)
        path = self.stale(h, {"pid": 0, "started": 0})
        aggregate_round(h.run, 1, False)
        assert [r["ok"] for r in results_by_judge(round_dir(h.run, 1))[1]] == [True]
        assert not path.exists()

    def test_a_marker_with_a_used_agent_still_gives_a_void(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        used = judge_seat(h.run, 1, 1, False)["agent_id"]
        marker = round_dir(h.run, 1) / "running_j2.json"
        marker.write_text(json.dumps({"pid": 0, "agent_id": used, "started": 0}))
        assert open_seats(h.run, 1, False) == [2]
        [void] = results_by_judge(round_dir(h.run, 1))[2]
        assert void["void"] and void["agent_id"] != used

    def test_a_damaged_stale_file_still_gives_a_void(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        (round_dir(h.run, 1) / "running_j1.json.abc.stale").write_text("{not json")
        open_seats(h.run, 1, False)
        assert results_by_judge(round_dir(h.run, 1))[1][0]["void"]

    def test_a_stale_attempt_counts_against_the_void_cap(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--exit", "1"])
        h.brief(1, "cand")
        for _ in range(3):
            judge_seat(h.run, 1, 1, False)
        marker = round_dir(h.run, 1) / "running_j1.json"
        marker.write_text(json.dumps({"pid": 0, "agent_id": "runner-last", "started": 0}))
        with pytest.raises(EvalError, match="voided attempts"):
            open_seats(h.run, 1, False)
        assert len(results_by_judge(round_dir(h.run, 1))[1]) == 3
        assert not marker.exists()


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
    def test_a_reply_built_to_break_the_parser_is_rejected(self, h: Harness, code: str) -> None:
        self.judge_script(h, code)
        r = judge_seat(h.run, 1, 1, False)
        assert not r["ok"] and not r.get("void") and r["errors"][0].startswith("unparseable")
        assert not list(round_dir(h.run, 1).glob("running_j*"))

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
        marker = round_dir(h.run, 1) / "running_j1.json"
        judge = 0
        try:
            deadline = time.monotonic() + 60
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            judge = int(pid_file.read_text())
            assert marker.exists()  # held for as long as the judge runs
        finally:
            proc.kill()
            proc.wait()
            if judge:
                stop_group(judge)
        assert marker.exists()
        assert open_seats(h.run, 1, False) == [1, 2]
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "stopped before recording it" in void["errors"][0]


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
    def test_a_dotted_key_is_refused(self, h: Harness) -> None:
        h.init()
        data = json.loads((h.run / "config.json").read_text())
        data["transcript"]["tool_block_type"] = "nothing"
        data["transcript.tool_block_type"] = "tool_use"  # would shadow it in a flattened view
        relock(h.run, data)
        with pytest.raises(EvalError, match="holds a dot"):
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


class TestPlantedMarkers:
    def run_mode(self, h: Harness, *args: str) -> Path:
        init_judge(h, [sys.executable, str(STUB), *args])
        h.brief(1, "cand")
        return round_dir(h.run, 1)

    def test_planted_stale_files_never_pass_the_void_cap(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        for i in range(5):
            (rdir / f"running_j1.json.m{i}.stale").write_text("{}")
        with pytest.raises(EvalError, match="voided attempts"):
            open_seats(h.run, 1, False)
        assert len(results_by_judge(rdir)[1]) == 3

    def test_a_seat_at_its_cap_never_fails_another_seat(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        for i in range(5):
            (rdir / f"running_j1.json.m{i}.stale").write_text("{}")
        assert open_seats(h.run, 1, False, [2]) == [2]
        assert len(results_by_judge(rdir)[1]) == 3
        assert not list(rdir.glob("*.stale"))

    def test_a_stale_file_for_an_unknown_seat_is_dropped(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        (rdir / "running_j999.json.x.stale").write_text("{}")
        assert open_seats(h.run, 1, False) == [1, 2]
        assert not (rdir / "running_j999.json.x.stale").exists()
        assert 999 not in results_by_judge(rdir)

    def test_a_marker_gives_nothing_but_holder_and_time(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        held = {"pid": 0, "started": 0, "argv": ["x"], "agent_id": "author-pick", "note": "CANARY"}
        (rdir / "running_j1.json.a.stale").write_text(json.dumps(held))
        open_seats(h.run, 1, False)
        [void] = results_by_judge(rdir)[1]
        assert void["agent_id"].startswith("runner-") and "author-pick" not in json.dumps(void)
        assert set(void["runner"]) == {"pid", "started", "argv", "stale", "failure"}

    def test_a_linked_stale_file_is_not_read(self, h: Harness) -> None:
        rdir = self.run_mode(h)
        outside = h.tmp / "secret.json"
        outside.write_text(json.dumps({"pid": 1, "started": 1, "argv": ["OUTSIDE-CANARY"]}))
        (rdir / "running_j2.json.lk.stale").symlink_to(outside)
        open_seats(h.run, 1, False)
        assert "OUTSIDE-CANARY" not in json.dumps(results_by_judge(rdir))
        assert not (rdir / "running_j2.json.lk.stale").is_symlink()

    def test_stale_files_mean_nothing_in_host_mode(self, h: Harness) -> None:
        h.init(judges_per_persona=2)
        h.brief(1, "cand")
        rdir = round_dir(h.run, 1)
        (rdir / "running_j1.json.v.stale").write_text(json.dumps({"agent_id": "A1"}))
        meta = h.keys(1)["judges"][1]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        record_reply(h.run, 1, 2, "a2", judge_json(meta, h.texts(1, meta), sc), False)
        with pytest.raises(EvalError, match="no real attempt"):
            aggregate_round(h.run, 1, False)
        assert 1 not in results_by_judge(rdir)  # seat 1 empty: the planted file voided nothing

    @pytest.mark.parametrize("name", ["running_j1.json", "running_j1.json.f.stale", "brief_j1.md"])
    def test_a_fifo_never_blocks_the_runner(self, h: Harness, name: str) -> None:
        rdir = self.run_mode(h)
        target = rdir / name
        target.unlink(missing_ok=True)
        os.mkfifo(target)
        outcome: list[Any] = []

        def go() -> None:
            try:
                open_seats(h.run, 1, False)
                outcome.append(judge_seat(h.run, 1, 1, False))
            except EvalError as exc:
                outcome.append(exc)

        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        thread.join(30)
        assert not thread.is_alive(), "blocked on a FIFO"
        if name == "brief_j1.md":
            assert isinstance(outcome[0], EvalError) and "not a regular file" in str(outcome[0])
        else:
            assert outcome[0]["ok"] and not target.exists()


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


class TestDraftModes:
    def test_a_draft_replacing_a_private_file_starts_private(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "keep.json"
        path.write_text("{}")
        os.chmod(path, 0o600)
        modes: list[int] = []
        real = os.open

        def spy(file: Any, flags: int, mode: int = 0o777, **kw: Any) -> int:
            if flags & os.O_CREAT:
                modes.append(mode)
            return real(file, flags, mode, **kw)

        monkeypatch.setattr(store.os, "open", spy)
        dump_json(path, {"a": 1})
        assert modes == [0o600]
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_the_fallback_keeps_the_mode_and_refuses_links(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(store.os, "supports_dir_fd", set())
        path = tmp_path / "keep.json"
        path.write_text("{}")
        os.chmod(path, 0o644)
        dump_json(path, {"a": 1})
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        assert json.loads(path.read_text()) == {"a": 1}
        (tmp_path / "elsewhere").mkdir()
        (tmp_path / "dir").symlink_to(tmp_path / "elsewhere")
        with pytest.raises(EvalError, match="symbolic link"):
            write_file(tmp_path / "dir" / "x.md", "x")

    def test_an_empty_transcript_root_argument_is_accepted(self) -> None:
        args = cli._parser().parse_args(["mcp", "--transcript-root"])
        assert args.transcript_root == [""]
