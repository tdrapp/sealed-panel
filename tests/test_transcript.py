"""Replies read from JSONL transcripts: the brief id, the agent id, and unrecorded agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json
from sealed_panel.aggregate import aggregate_round
from sealed_panel.cli import main
from sealed_panel.errors import EvalError
from sealed_panel.record import last_reply, record_reply
from sealed_panel.store import round_dir


def entry(role: str, text: str, agent: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"type": role, "message": {"content": [{"type": "text", "text": text}]}}
    if agent is not None:
        row["agentId"] = agent
    return row


def write_jsonl(path: Path, rows: list[dict[str, Any]], tail: str = "") -> Path:
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n" + tail)
    return path


def fake_transcript(folder: Path, agent: str, text: str) -> Path:
    return write_jsonl(folder / f"{agent}.output", [entry("user", text, agent)])


class TestTranscript:
    def test_last_reply_is_the_last_assistant_text(self, tmp_path: Path) -> None:
        rows = [
            entry("user", "brief"),
            entry("assistant", "draft"),
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "x"}]}},
            entry("assistant", '{"items": {}}'),
        ]
        p = write_jsonl(tmp_path / "agent.jsonl", rows, "not json\n")
        assert last_reply(p) == '{"items": {}}'

    def test_no_reply_is_an_error_not_a_guess(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.jsonl"
        p.write_text(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
        with pytest.raises(EvalError):
            last_reply(p)

    def test_last_reply_ignores_a_trailing_user_entry(self, tmp_path: Path) -> None:
        p = write_jsonl(
            tmp_path / "t.jsonl", [entry("assistant", "the reply"), entry("user", "not a reply")]
        )
        assert last_reply(p) == "the reply"


class TestTranscriptRefusals:
    def test_transcript_agent_id_must_match(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        brief1 = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        t = write_jsonl(
            h.tmp / "judge.jsonl",  # carries the real brief, so only the id mismatch can refuse
            [entry("user", brief1, "real1"), entry("assistant", "no json", "real1")],
        )
        base = ["record", str(h.run), "--round", "1", "--judge", "1"]
        rc_ok = main([*base, "--agent-id", "real1", "--transcript", str(t)])
        assert rc_ok == 0, "the matching id is recorded (as a rejected reply)"
        t2 = h.tmp / "judge2.jsonl"
        t2.write_text(t.read_text().replace("real1", "real2").replace("no json", "no json 2"))
        rc = main([*base, "--agent-id", "made-up", "--transcript", str(t2)])
        assert rc == 2

    def test_transcript_must_hold_the_brief_id(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        t = fake_transcript(h.tmp / "tasks", "a1", "some other brief")
        with pytest.raises(EvalError, match="verbatim"):
            record_reply(h.run, 1, 1, "a1", "{}", False, "transcript", t)

    def test_an_unrecorded_agent_on_a_brief_blocks_aggregate(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        rdir = round_dir(h.run, 1)
        folder = h.tmp / "tasks"
        for meta in h.keys(1)["judges"]:
            raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
            agent = f"rec{meta['judge']}"
            brief = (rdir / f"brief_j{meta['judge']}.md").read_text()
            t = fake_transcript(folder, agent, brief)
            record_reply(h.run, 1, meta["judge"], agent, raw, False, "transcript", t)
        fake_transcript(folder, "hidden", (rdir / "brief_j1.md").read_text())  # a 4th, kept quiet
        with pytest.raises(EvalError, match="never recorded"):
            aggregate_round(h.run, 1, False)
