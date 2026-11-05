"""Replies read from JSONL transcripts: the brief id, the agent id, and unrecorded agents."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel.aggregate import aggregate_round
from sealed_panel.cli import main
from sealed_panel.config import load_config
from sealed_panel.errors import EvalError
from sealed_panel.record import judge_tools, last_reply, record_reply
from sealed_panel.spec import init_run, run_config
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


class TestJudgeTools:
    def tool_row(self, name: str, agent: str) -> dict[str, Any]:
        block = {"type": "tool_use", "id": "t1", "name": name, "input": {"file_path": "keys.json"}}
        return {"type": "assistant", "agentId": agent, "message": {"content": [block]}}

    def reply_with(self, h: Harness, tools: list[str], cfg_tool: str = "") -> dict[str, Any]:
        if cfg_tool:
            c = h.tmp / "c.toml"
            c.write_text(f'[transcript]\nhandback_tool = "{cfg_tool}"\n')
            sp = h.tmp / "spec.json"
            sp.write_text(json.dumps(make_spec()))
            init_run(h.run, sp, load_config(c))
        else:
            h.init()
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        sc = {r: BAD for r in meta["key"].values()} | {"candidate": FLAT}
        raw = judge_json(meta, h.texts(1, meta), sc)
        brief = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        rows = [entry("user", brief, "j1"), *[self.tool_row(n, "j1") for n in tools]]
        rows.append(entry("assistant", raw, "j1"))
        t = write_jsonl(h.tmp / "tasks" / "j1.output", rows)
        return record_reply(h.run, 1, 1, "j1", raw, False, "transcript", t)

    def test_a_judge_that_used_a_tool_is_rejected(self, h: Harness) -> None:
        r = self.reply_with(h, ["Read", "Bash"])
        assert not r["ok"]
        assert r["errors"][0].startswith("the judge used a tool (Bash, Read): a judge gets no")

    def test_a_judge_without_tools_is_read(self, h: Harness) -> None:
        assert self.reply_with(h, [])["ok"]

    def test_the_handback_tool_is_not_a_tool_use(self, h: Harness) -> None:
        assert self.reply_with(h, ["ReturnToCaller"], cfg_tool="ReturnToCaller")["ok"]
        assert judge_tools(h.tmp / "tasks" / "j1.output", run_config(h.run)) == []

    def test_any_other_tool_beside_the_handback_is_a_tool_use(self, h: Harness) -> None:
        r = self.reply_with(h, ["ReturnToCaller", "Read"], cfg_tool="ReturnToCaller")
        assert not r["ok"] and "(Read)" in r["errors"][0]
