"""The command line, end to end on a one-shot grade and a one-round loop."""

from __future__ import annotations

import io
import json
import runpy
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel.cli import main


def cli(*args: str) -> int:
    return main([str(a) for a in args])


def replies(h: Harness, rnd: int, scores: dict[str, Any], sealed: bool = False) -> list[Path]:
    out = []
    for meta in h.keys(rnd, sealed)["judges"]:
        sc = {r: scores.get(r, scores["candidate"]) for r in meta["key"].values()}
        p = h.tmp / f"reply_{'s' if sealed else 'r'}{rnd}_j{meta['judge']}.md"
        p.write_text(judge_json(meta, h.texts(rnd, meta, sealed), sc))
        out.append(p)
    return out


class TestCli:
    def test_full_run(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec(max_rounds=1, judges_per_persona=2)))
        assert cli("init", h.run, "--spec", sp) == 0
        args = ["--artefact", h.files["cand"], "--control", h.files["ctrl"]]
        assert cli("brief", h.run, "--round", 1, *args) == 0
        for i, p in enumerate(replies(h, 1, {"candidate": FLAT, "control": BAD}), start=1):
            rc = cli(
                "record", h.run, "--round", 1, "--judge", i, "--agent-id", f"p{i}", "--output", p
            )
            assert rc == 0
        assert cli("aggregate", h.run, "--round", 1) == 0
        capsys.readouterr()
        assert cli("status", h.run) == 0
        assert json.loads(capsys.readouterr().out)["decision"] == "CAP"
        assert cli("brief", h.run, "--round", 1, "--sealed", *args) == 0
        sealed = replies(h, 1, {"candidate": {"clarity": 5, "evidence": 4}, "control": BAD}, True)
        for i, p in enumerate(sealed, start=1):
            rc = cli(
                "record", h.run, "--round", 1, "--judge", i, "--agent-id", f"s{i}",
                "--output", p, "--sealed",
            )  # fmt: skip
            assert rc == 0
        assert cli("aggregate", h.run, "--round", 1, "--sealed") == 0
        capsys.readouterr()
        assert cli("report", h.run) == 0
        out = capsys.readouterr().out
        assert "Grade (sealed panel): clarity 5 (5/5); evidence 4 (4/4)" in out
        assert "repeat-test on the same text" in out

    def test_measure_and_stdin(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init(measured=[{"id": "tone", "description": "house style passes"}])
        h.brief(1, "cand")
        assert cli("measure", h.run, "--round", 1, "--id", "tone", "--value", "ok", "--pass") == 0
        assert "pass" in capsys.readouterr().out
        assert cli("measure", h.run, "--round", 1, "--id", "tone", "--value", "ok") == 2
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
        rc = cli("record", h.run, "--round", 1, "--judge", 1, "--agent-id", "x", "--output", "-")
        assert rc == 0
        assert "REJECTED judge 1" in capsys.readouterr().out

    def test_measure_needs_a_flag_without_threshold(self, h: Harness) -> None:
        h.init(measured=[{"id": "tone", "description": "house style passes"}])
        h.brief(1, "cand")
        assert cli("measure", h.run, "--round", 1, "--id", "tone", "--value", "ok", "--fail") == 0

    def test_refusals_exit_two(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        assert cli("status", h.tmp / "nowhere") == 2
        assert "sealed-panel:" in capsys.readouterr().err
        h.init()
        h.brief(1, "cand")
        missing = h.tmp / "missing.md"
        rc = cli(
            "record", h.run, "--round", 1, "--judge", 1, "--agent-id", "y", "--output", missing
        )
        assert rc == 2

    def test_config_flag(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = h.tmp / "c.toml"
        cfg.write_text("[quotes]\nmin_chars = 14\n")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        assert cli("init", h.tmp / "other", "--spec", sp, "--config", h.tmp / "absent.toml") == 2
        assert cli("init", h.run, "--spec", sp, "--config", cfg) == 0
        capsys.readouterr()
        assert cli("status", h.run) == 0
        assert "quotes.min_chars = 14 (default 12)" in capsys.readouterr().out

    def test_version_and_module_entry(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "sealed-panel 0.1.0" in capsys.readouterr().out
        monkeypatch.setattr(sys, "argv", ["sealed-panel", "status", "/nonexistent/run"])
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("sealed_panel", run_name="__main__")
        assert exc.value.code == 2
