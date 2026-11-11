"""The `sealed-panel rubrics` commands, and a run frozen by 0.1.0 under this version."""

from __future__ import annotations

import io
import shutil
import sys
from pathlib import Path

import pytest

from sealed_panel import library
from sealed_panel.cli import main
from sealed_panel.errors import EvalError
from sealed_panel.record import record_reply

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "run-0.1.0"
EXAMPLE = ROOT / "examples" / "announcement"


def run(capsys: pytest.CaptureFixture[str], *args: object) -> tuple[int, str]:
    rc = main([str(a) for a in args])
    out = capsys.readouterr()
    return rc, out.out + out.err


def add(lib: Path, change: dict[str, object], version: int = 1) -> str:
    path = library.add_candidate(
        lib,
        {
            "kind": "judges_disagree",
            "dimension": "ask",
            "run": {"rubric_sha256": "0" * 64, "template": {"id": "message", "version": version}},
            "evidence": [{"round": 1, "judges": [1, 2], "quotes": []}],
            "proposed_change": change,
        },
    )
    assert path is not None
    return path.stem


REMOVE = {"action": "remove_dimension", "dimension": "brevity"}


class TestRubricsCli:
    def test_list(self, capsys: pytest.CaptureFixture[str], private_library: Path) -> None:
        cid = add(private_library, REMOVE)
        library.reject(private_library, cid, "keep brevity")
        add(private_library, REMOVE, version=2)
        (private_library / "broken.json").write_text("{nope")
        rc, out = run(capsys, "rubrics", "list")
        assert rc == 0
        assert f"library: {private_library}" in out
        assert "inbox: 1 open candidate(s)" in out
        assert "rejected before (v1): keep brevity" in out
        assert "warning: skipped" in out

    def test_suggest(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        rc, out = run(capsys, "rubrics", "suggest", "--kind", "E-Mail")
        assert rc == 0
        assert out.startswith("message v1 (built-in): An email")
        assert "  ask " in out and "  brevity " in out
        rc, out = run(capsys, "rubrics", "suggest", "--kind", "limerick")
        assert rc == 0
        assert "no template for kind 'limerick'" in out
        assert "  essay: " in out and "  cv: " in out
        assert run(capsys, "rubrics", "suggest", "--kind", "email", "--top", "0")[0] == 2

    def test_show_and_use(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        rc, out = run(capsys, "rubrics", "show", "pitch")
        assert rc == 0 and out.startswith("# pitch v1, built-in")
        assert run(capsys, "rubrics", "show", "nope")[0] == 2
        out_path = tmp_path / "spec.json"
        rc, out = run(capsys, "rubrics", "use", "essay", "--out", out_path)
        assert rc == 0 and "fill every TODO(sealed-panel) marker" in out
        rc, out = run(capsys, "init", tmp_path / "run", "--spec", out_path)
        assert rc == 2 and "fill the TODO(sealed-panel) markers first" in out

    def test_promote_needs_a_person(
        self,
        capsys: pytest.CaptureFixture[str],
        private_library: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cid = add(private_library, REMOVE)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        rc, out = run(capsys, "rubrics", "promote", cid)
        assert rc == 2 and "pass --yes" in out
        assert "dimension brevity" in out and "(removed)" in out
        assert not (private_library / "message.json").exists()

        class Tty(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "stdin", Tty(""))  # end of input at the prompt
        rc, out = run(capsys, "rubrics", "promote", cid)
        assert rc == 2 and "not confirmed" in out and "Traceback" not in out
        monkeypatch.setattr(sys, "stdin", Tty("wrong\n"))
        assert run(capsys, "rubrics", "promote", cid)[0] == 2
        assert not (private_library / "message.json").exists()
        monkeypatch.setattr(sys, "stdin", Tty(cid + "\n"))
        rc, out = run(capsys, "rubrics", "promote", cid)
        assert rc == 0 and "(v2)" in out

    def test_promote_yes_and_reject(
        self, capsys: pytest.CaptureFixture[str], private_library: Path
    ) -> None:
        cid = add(private_library, REMOVE)
        rc, out = run(capsys, "rubrics", "promote", cid, "--yes", "--library", private_library)
        assert rc == 0 and "(v2)" in out
        rc, out = run(capsys, "rubrics", "list")
        assert "user (overrides built-in v1)" in out
        cid = add(
            private_library, {"action": "remove_dimension", "dimension": "evidence"}, version=2
        )
        rc, out = run(capsys, "rubrics", "reject", cid, "--reason", "keep it")
        assert rc == 0 and "history" in out
        assert run(capsys, "rubrics", "promote", "../../x", "--yes")[0] == 2


class TestRunFrozenByVersion010:
    """A run initialised and aggregated by sealed-panel 0.1.0 is read by this version and never
    written to: every write command refuses it, naming 0.1.0."""

    def test_status_and_report_read_it(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path
    ) -> None:
        r = tmp_path / "run"
        shutil.copytree(FIXTURE, r)
        rc, out = run(capsys, "status", r)
        assert rc == 0 and '"CAP"' in out and "frozen by sealed-panel 0.1.0" in out
        rc, out = run(capsys, "report", r)
        assert rc == 0 and "library" not in out.splitlines()[2]

    def test_every_write_command_refuses_it(
        self, capsys: pytest.CaptureFixture[str], tmp_path: Path, private_library: Path
    ) -> None:
        r = tmp_path / "run"
        shutil.copytree(FIXTURE, r)
        before = sorted(p.relative_to(r) for p in r.rglob("*"))
        art = ["--artefact", EXAMPLE / "draft_v1.md", "--control", EXAMPLE / "control.md"]
        reply = tmp_path / "reply.json"
        reply.write_text("{}")
        commands: list[tuple[object, ...]] = [
            ("brief", r, "--sealed", *art),
            ("brief", r, "--round", "2", *art),
            ("record", r, "--sealed", "--judge", "1", "--agent-id", "x", "--output", reply),
            ("judge", r, "--sealed"),
            ("measure", r, "--sealed", "--id", "length", "--value", "130"),
            ("aggregate", r, "--round", "1"),
            ("learning", "learn", r),
            ("feedback", r, "--note", "too kind"),
        ]
        for args in commands:
            rc, out = run(capsys, *args)
            assert rc == 2 and "frozen by sealed-panel 0.1.0, before 0.4.0" in out, args
            assert "Finish it with sealed-panel 0.1.0" in out, args
        with pytest.raises(EvalError, match=r"frozen by sealed-panel 0\.1\.0"):
            record_reply(r, 1, 1, "x", "{}", True)
        assert sorted(p.relative_to(r) for p in r.rglob("*")) == before
        assert not private_library.exists()
