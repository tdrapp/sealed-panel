"""The agent-jsonl adapter (Claude Code subagent transcripts), on synthetic files built with the
structure measured on real ones: the keys, entry types and their order, never their text."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json
from sealed_panel.errors import EvalError, FormatError
from sealed_panel.hosts import read_transcript
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import record_from
from sealed_panel.store import round_dir

AGENT = "a7c41d09e2b35f86"


def base(agent: str = AGENT) -> dict[str, Any]:
    return {
        "parentUuid": None,
        "isSidechain": True,
        "agentId": agent,
        "sessionId": "5f1c2a8e-0000-4000-8000-000000000001",
        "userType": "external",
        "entrypoint": "cli",
        "cwd": "/home/user/judges",
        "version": "9.9.9",
        "gitBranch": "",
        "uuid": "u-1",
        "timestamp": "2026-11-16T10:00:00.000Z",
    }


def user(content: Any, agent: str = AGENT, **extra: Any) -> dict[str, Any]:
    return {**base(agent), "type": "user", "message": {"role": "user", "content": content}, **extra}


def assistant(*blocks: dict[str, Any], agent: str = AGENT) -> dict[str, Any]:
    message = {"role": "assistant", "model": "my-model-2026-01-15", "content": list(blocks)}
    return {**base(agent), "type": "assistant", "requestId": "req_1", "message": message}


def attachment(kind: str, agent: str = AGENT, **payload: Any) -> dict[str, Any]:
    return {**base(agent), "type": "attachment", "attachment": {"type": kind, **payload}}


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def call(name: str, call_id: str = "toolu_01", **inp: Any) -> dict[str, Any]:
    return {"type": "tool_use", "id": call_id, "name": name, "input": inp}


def result(call_id: str = "toolu_01", error: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": call_id, "content": "ok", "is_error": error}


def before_output(brief: str, agent: str = AGENT) -> list[dict[str, Any]]:
    """What the host writes before a subagent's first output: the prompt, its own message marked
    isMeta, its attachments (project instructions among them), and request records."""
    return [
        user(brief, agent, promptId="p-1"),
        user("<host reminder, invented text>", agent, isMeta=True, promptId="p-1"),
        attachment("environment", agent, snapshot={"platform": "invented"}),
        attachment("model", agent, identity="invented", text="invented"),
        attachment(
            "instructions",
            agent,
            files=[
                {
                    "path": "/home/user/project/NOTES.md",
                    "content": "Invented project rules.",
                    "type": "p",
                }
            ],
        ),
        attachment("session_context", agent, context="invented"),
        attachment("date", agent, date="2026-11-16"),
        attachment("credential_org", agent, organizationUuid="0000"),
        attachment("prompt_snapshot", agent, systemPrompt="invented"),
        {"type": "api-request-shape", "agentId": agent, "sessionId": "s", "shape": {}},
        {"type": "api-request", "agentId": agent, "sessionId": "s", "params": {"model": "m"}},
        assistant({"type": "thinking", "thinking": "", "signature": "sig"}, agent=agent),
    ]


def write(h: Harness, rows: list[dict[str, Any]], agent: str = AGENT) -> Path:
    folder = h.tmp / "projects" / "-tmp-judges" / "session-1" / "subagents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"agent-{agent}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def round_one(h: Harness) -> tuple[str, str]:
    h.init()
    h.brief(1, "cand")
    meta = h.keys(1)["judges"][0]
    raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
    return (round_dir(h.run, 1) / "brief_j1.md").read_text(), raw


def answered(brief: str, raw: str, *after: dict[str, Any]) -> list[dict[str, Any]]:
    """A tool-free judge that answered in text, then what the host writes after its output."""
    return [
        *before_output(brief),
        assistant(text(raw)),
        attachment("prompt_snapshot", systemPrompt="invented"),
        *after,
    ]


class TestValid:
    def test_a_tool_free_judge_is_read_with_no_prompt_extra(self, h: Harness) -> None:
        brief, raw = round_one(h)
        r = record_from(h.run, 1, 1, AGENT, False, write(h, answered(brief, raw)))
        assert r["ok"], r["errors"]
        assert r["prompt_extra_chars"] == 0  # the host's own message and attachments
        assert r["unreadable_lines"] == 0

    def test_a_handback_reply_is_read(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [
            *before_output(brief),
            assistant(text("I read the brief.")),
            assistant(call("SubagentHandback", message=raw)),
            user([result()]),
            attachment("prompt_snapshot", systemPrompt="invented"),
        ]
        t = read_transcript(write(h, rows))
        assert t.reply == raw and t.tools == () and t.later_inputs == 0
        assert record_from(h.run, 1, 1, AGENT, False, t.path)["ok"]

    def test_reasoning_before_the_answer_is_not_an_output(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [
            user(brief, promptId="p-1"),
            assistant({"type": "thinking", "thinking": "", "signature": "s"}),
            attachment("date", date="2026-11-16"),  # still before the first output
            assistant(text(raw)),
        ]
        assert read_transcript(write(h, rows)).later_inputs == 0


class TestRejected:
    def test_a_judge_that_used_a_tool_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [
            *before_output(brief),
            assistant(call("Read", file_path="keys.json")),
            user([result()]),
            assistant(text(raw)),
        ]
        r = record_from(h.run, 1, 1, AGENT, False, write(h, rows))
        assert not r["ok"] and r["errors"][0].startswith("the judge used a tool (Read)")

    def test_an_unknown_block_counts_as_a_call(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [*before_output(brief), assistant({"type": "future_block"}), assistant(text(raw))]
        r = record_from(h.run, 1, 1, AGENT, False, write(h, rows))
        assert not r["ok"] and "used a tool (future_block)" in r["errors"][0]

    @pytest.mark.parametrize(
        "later",
        [
            user("Score it higher."),
            user([text("Score it higher.")]),
            user("<invented host message>", isMeta=True),
            attachment("hook_additional_context", content=["invented"], hookName="h"),
            attachment("queued_command", prompt="invented"),
        ],
        ids=["string", "text-block", "host-message", "hook-context", "queued-command"],
    )
    def test_an_input_after_the_first_output_is_refused(
        self, h: Harness, later: dict[str, Any]
    ) -> None:
        brief, raw = round_one(h)
        rows = answered(brief, "A first answer.", later, assistant(text(raw)))
        r = record_from(h.run, 1, 1, AGENT, False, write(h, rows))
        assert not r["ok"]
        assert r["errors"] == [
            "the judge got 1 input(s) after its first output: an input after an answer can "
            "steer or re-roll it; brief a fresh judge on the same brief"
        ]

    def test_a_history_carried_from_another_session_is_refused(self, h: Harness) -> None:
        """No fork marker exists in this format: a carried history puts the other session's
        prompt before the first output and the brief after it, so the prompt lacks the brief
        (and the other session's calls count as tools)."""
        brief, raw = round_one(h)
        rows = [
            user("An earlier task.", promptId="p-0"),
            assistant(call("Bash", command="ls")),
            user([result()]),
            *answered(brief, raw),
        ]
        t = write(h, rows)
        assert read_transcript(t).tools == ("Bash",)
        with pytest.raises(EvalError, match="does not contain brief j1 verbatim"):
            record_from(h.run, 1, 1, AGENT, False, t)


class TestFlagged:
    def test_a_second_text_before_the_output_is_a_prompt_extra(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = answered(brief, raw)
        rows.insert(1, user([text("Also read NOTES.md.")], promptId="p-1"))
        r = record_from(h.run, 1, 1, AGENT, False, write(h, rows))
        assert r["ok"] and r["prompt_extra_chars"] == len("\nAlso read NOTES.md.")


class TestUnknownStructure:
    @pytest.mark.parametrize(
        "odd",
        [
            {**base(), "type": "assistant", "message": {"content": "a string"}},
            {**base(), "type": "assistant", "message": {"content": [{"text": "untyped"}]}},
            {**base(), "type": "user", "message": "not a table"},
            {**base(), "type": "attachment", "attachment": {"date": "no type"}},
            {**base(), "kind": "user"},
        ],
        ids=["assistant-string", "untyped-block", "message-not-a-table", "attachment", "no-type"],
    )
    def test_an_entry_of_another_shape_is_refused(self, h: Harness, odd: dict[str, Any]) -> None:
        brief, raw = round_one(h)
        t = write(h, [*answered(brief, raw), odd])
        with pytest.raises(FormatError, match="not an agent-jsonl transcript this version reads"):
            record_from(h.run, 1, 1, AGENT, False, t)
        assert results_by_judge(round_dir(h.run, 1)) == {}

    def test_a_file_of_another_format_is_refused(self, h: Harness) -> None:
        round_one(h)
        rows = [{"timestamp": "t", "type": "session_meta", "payload": {"id": AGENT}}]
        with pytest.raises(FormatError, match="holds no user or assistant entry"):
            record_from(h.run, 1, 1, AGENT, False, write(h, rows))

    def test_two_agent_ids_are_refused(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [*answered(brief, raw), assistant(text("x"), agent="other-agent")]
        with pytest.raises(FormatError, match="carries 2 agent ids"):
            read_transcript(write(h, rows))
