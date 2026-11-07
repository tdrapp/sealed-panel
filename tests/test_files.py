"""How a run touches the disk, and what a hostile judge can make it do: whole writes, no link
followed, bounded output, leftovers of a killed runner, replies built to break the reader."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel import brief as brief_module
from sealed_panel import cli, library, record, report, runner
from sealed_panel.aggregate import aggregate_round
from sealed_panel.config import Config, ConfigError, default_data, load_config, merge
from sealed_panel.errors import EvalError
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import extract_json, record_from, record_reply
from sealed_panel.report import render_report
from sealed_panel.runner import SeatTaken, judge_round, judge_seat, open_seats
from sealed_panel.spec import init_run
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
        os.chmod(path, 0o600)
        dump_json(path, {"a": 1})
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
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

    def test_a_findings_link_planted_after_the_check_is_not_followed(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(report, "refuse_links", lambda *paths: None)
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
        init_judge(h, [sys.executable, str(STUB), "--sleep", "30"])
        h.brief(1, "cand")

        def interrupted(seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(runner.time, "sleep", interrupted)
        start = time.monotonic()
        with pytest.raises(KeyboardInterrupt):
            judge_seat(h.run, 1, 1, False)
        assert time.monotonic() - start < 15
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
        assert void["void"] and void["agent_id"] == "runner-dead"
        assert not path.exists()

    def test_a_marker_moved_aside_becomes_a_void_at_aggregate(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB)])
        h.brief(1, "cand")
        judge_round(h.run, 1, False)
        path = self.stale(h, {"pid": 0, "agent_id": "runner-dead", "started": 0})
        entry = aggregate_round(h.run, 1, False)
        assert [v["agent_id"] for v in entry["voided"]] == ["runner-dead"]
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

    def test_a_stale_attempt_is_recorded_even_at_the_void_cap(self, h: Harness) -> None:
        init_judge(h, [sys.executable, str(STUB), "--exit", "1"])
        h.brief(1, "cand")
        for _ in range(3):
            judge_seat(h.run, 1, 1, False)
        marker = round_dir(h.run, 1) / "running_j1.json"
        marker.write_text(json.dumps({"pid": 0, "agent_id": "runner-last", "started": 0}))
        with pytest.raises(EvalError, match="voided attempts"):
            open_seats(h.run, 1, False)
        assert len(results_by_judge(round_dir(h.run, 1))[1]) == 4
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
        init_judge(h, [sys.executable, str(STUB), "--sleep", "1"])
        h.brief(1, "cand")
        code = (
            "import sys; from pathlib import Path; from sealed_panel.runner import judge_seat; "
            f"judge_seat(Path({str(h.run)!r}), 1, 1, False)"
        )
        env = {**os.environ, "PYTHONPATH": str(SRC.parent)}
        proc = subprocess.Popen([sys.executable, "-c", code], env=env)
        marker = round_dir(h.run, 1) / "running_j1.json"
        for _ in range(200):
            if marker.exists():
                break
            time.sleep(0.05)
        proc.kill()
        proc.wait()
        assert marker.exists()
        assert open_seats(h.run, 1, False) == [1, 2]
        [void] = results_by_judge(round_dir(h.run, 1))[1]
        assert void["void"] and "stopped before recording it" in void["errors"][0]
