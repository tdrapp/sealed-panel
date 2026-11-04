"""The `sealed-panel rubrics` commands, end to end."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sealed_panel import library
from sealed_panel.cli import main


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    rc = main([str(a) for a in args])
    out = capsys.readouterr()
    return rc, out.out + out.err


def add(lib: Path, change: dict[str, object]) -> str:
    return library.add_candidate(
        lib,
        {
            "kind": "sharpen_anchor",
            "dimension": "ask",
            "run": {"rubric_sha256": "0" * 64, "template": {"id": "message", "version": 1}},
            "evidence": [{"round": 1, "judges": [1, 2], "quotes": ["q"]}],
            "proposed_change": change,
        },
    ).stem


class TestRubricsCli:
    def test_list(self, capsys: pytest.CaptureFixture[str], private_library: Path) -> None:
        add(private_library, {"action": "remove_dimension", "dimension": "brevity"})
        rc, out = run(capsys, "rubrics", "list")
        assert rc == 0
        assert "message" in out and "built-in" in out
        assert "inbox: 1 open candidate(s)" in out
        assert "sharpen_anchor" in out

    def test_suggest(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        text = tmp_path / "t.md"
        text.write_text("Please reply before the deadline.")
        rc, out = run(capsys, "rubrics", "suggest", "--kind", "email", "--text", text, "--top", "2")
        assert rc == 0
        lines = out.strip().splitlines()
        assert len(lines) == 2
        assert lines[0].split()[1] == "message"
        assert "kind 'email' matches" in lines[0]
        rc, out = run(capsys, "rubrics", "suggest", "--kind", "email", "--top", "0")
        assert rc == 2

    def test_show(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc, out = run(capsys, "rubrics", "show", "pitch")
        assert rc == 0
        assert out.startswith("# pitch v1, built-in")
        assert run(capsys, "rubrics", "show", "nope")[0] == 2

    def test_use(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        out_path = tmp_path / "spec.json"
        rc, out = run(capsys, "rubrics", "use", "essay", "--out", out_path)
        assert rc == 0
        assert "fill every TODO(sealed-panel) marker" in out
        assert json.loads(out_path.read_text())["template"] == {"id": "essay", "version": 1}
        rc, out = run(capsys, "init", tmp_path / "run", "--spec", out_path)
        assert rc == 2
        assert "fill the TODO(sealed-panel) markers first" in out

    def test_promote_and_reject(
        self, capsys: pytest.CaptureFixture[str], private_library: Path, tmp_path: Path
    ) -> None:
        cid = add(private_library, {"action": "remove_dimension", "dimension": "brevity"})
        rc, out = run(capsys, "rubrics", "promote", cid, "--library", private_library)
        assert rc == 0
        assert "(v2)" in out
        rc, out = run(capsys, "rubrics", "list")
        assert "user (overrides built-in v1)" in out
        cid = add(private_library, {"action": "remove_dimension", "dimension": "evidence"})
        rc, out = run(capsys, "rubrics", "reject", cid, "--reason", "keep it")
        assert rc == 0
        assert "history" in out
        assert run(capsys, "rubrics", "promote", cid)[0] == 2
