"""The rollout-jsonl adapter (Codex CLI session files), on synthetic files built with the
structure measured on real ones: the line types, payload keys and their order, never their
text."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel.errors import EvalError, FormatError
from sealed_panel.hosts import read_transcript, rollout_jsonl
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import record_from
from sealed_panel.run import run_config
from sealed_panel.settings import load_config
from sealed_panel.steps import aggregate_round, init_run
from sealed_panel.store import round_dir

SESSION = "019a7c41-d09e-7b35-8f86-000000000001"
PARENT = "019a7c41-d09e-7b35-8f86-000000000000"
STAMP = "2026-11-16T10:00:00.000Z"


def line(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"timestamp": STAMP, "type": kind, "payload": payload}


def session(sid: str = SESSION, parent: str | None = None, fork: bool = False) -> dict[str, Any]:
    p: dict[str, Any] = {
        "id": sid,
        "session_id": sid,
        "timestamp": STAMP,
        "cwd": "/home/user/empty",
        "originator": "codex_exec",
        "cli_version": "9.9.9",
        "source": "exec",
        "model_provider": "provider",
        "history_mode": "full",
        "base_instructions": {"text": "Invented host instructions.", "provenance": {"type": "x"}},
    }
    if parent is not None:
        p["parent_thread_id"] = parent
        p["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent, "depth": 1}}}
    if fork:
        p["forked_from_id"] = parent
        p["subagent_history_start_ordinal"] = 4
    return line("session_meta", p)


def message(role: str, text: str) -> dict[str, Any]:
    block = "output_text" if role == "assistant" else "input_text"
    return line(
        "response_item",
        {"type": "message", "id": "msg", "role": role, "content": [{"type": block, "text": text}]},
    )


def item(kind: str, **fields: Any) -> dict[str, Any]:
    return line("response_item", {"type": kind, "id": "it", **fields})


def event(kind: str) -> dict[str, Any]:
    return line("event_msg", {"type": kind, "turn_id": "turn-1"})


def opening(prompt: str, *before: dict[str, Any], **who: Any) -> list[dict[str, Any]]:
    """A session up to the judge's prompt: the host's tool declarations and developer
    messages, then the user messages."""
    return [
        session(**who),
        event("task_started"),
        item("additional_tools", role="developer", tools=[]),
        message("developer", "<invented permissions>"),
        message("developer", "<invented collaboration notes>"),
        *before,
        message("user", prompt),
        line("turn_context", {"cwd": "/home/user/empty", "model": "my-model-2026-01-15"}),
        item("reasoning", summary=[], encrypted_content="x"),
    ]


def closing(reply: str) -> list[dict[str, Any]]:
    return [message("assistant", reply), event("token_count"), event("task_complete")]


def write(h: Harness, rows: list[dict[str, Any]], name: str = SESSION) -> Path:
    folder = h.tmp / "sessions" / "2026" / "11" / "16"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"rollout-2026-11-16T10-00-00-{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def round_one(h: Harness) -> tuple[str, str]:
    c = h.tmp / "c.toml"
    c.write_text('[transcript]\nformat = "rollout-jsonl"\n')
    spec = h.tmp / "spec.json"
    spec.write_text(json.dumps(make_spec()))
    init_run(h.run, spec, load_config(c))
    h.brief(1, "cand")
    meta = h.keys(1)["judges"][0]
    raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
    return (round_dir(h.run, 1) / "brief_j1.md").read_text(), raw


def recorded(h: Harness, rows: list[dict[str, Any]], agent: str = SESSION) -> dict[str, Any]:
    return dict(record_from(h.run, 1, 1, agent, False, write(h, rows)))


class TestValid:
    def test_a_one_turn_tool_free_judge_is_read(self, h: Harness) -> None:
        brief, raw = round_one(h)
        assert run_config(h.run).transcript_format == "rollout-jsonl"
        r = recorded(h, [*opening(brief), *closing(raw)])
        assert r["ok"], r["errors"]
        assert r["prompt_extra_chars"] == 0  # the developer messages are the host's own

    def test_a_subagent_judge_given_its_task_by_message_is_read(self, h: Harness) -> None:
        brief, raw = round_one(h)
        task = item(
            "agent_message",
            author="/root",
            recipient="/root/judge",
            content=[{"type": "input_text", "text": brief}],
        )
        rows = [*opening("", task, parent=PARENT), *closing(raw)]
        r = recorded(h, rows)
        assert r["ok"], r["errors"]


class TestFlagged:
    def test_project_instructions_are_a_prompt_extra(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rules = "# Project instructions\nInvented rules for this folder."
        r = recorded(h, [*opening(brief, message("user", rules)), *closing(raw)])
        assert r["ok"] and r["prompt_extra_chars"] >= len("Invented rules for this folder.")


class TestRejected:
    def test_a_judge_that_ran_a_command_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        calls = [
            item("function_call", name="shell", arguments="{}", call_id="c1"),
            item("function_call_output", call_id="c1", output="keys"),
        ]
        r = recorded(h, [*opening(brief), *calls, *closing(raw)])
        assert not r["ok"]
        assert r["errors"][0].startswith("the judge used a tool (function_call_output, shell)")

    def test_an_unknown_item_counts_as_a_call(self, h: Harness) -> None:
        brief, raw = round_one(h)
        r = recorded(h, [*opening(brief), item("future_item"), *closing(raw)])
        assert not r["ok"] and "used a tool (future_item)" in r["errors"][0]

    def test_a_fork_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [
            *opening(brief, parent=PARENT, fork=True),
            *closing(raw),
        ]
        rows.insert(1, session(PARENT))  # the parent's history follows the fork's record
        r = recorded(h, rows)
        assert not r["ok"]
        assert r["errors"] == [
            "the judge's session is a fork: it carries another session's history, which can "
            "hold the run's labels or other judges' replies; brief a fresh judge on the same "
            "brief"
        ]

    @pytest.mark.parametrize("marked", [True, False], ids=["fork-keys", "second-record"])
    def test_either_fork_sign_is_enough(self, h: Harness, marked: bool) -> None:
        brief, raw = round_one(h)
        rows = [*opening(brief, parent=PARENT, fork=marked), *closing(raw)]
        if not marked:
            rows.insert(1, session(PARENT))
        assert read_transcript(write(h, rows), run_config(h.run)).fork

    def test_a_second_turn_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [
            *opening(brief),
            *closing("A first answer."),
            event("task_started"),
            message("user", "Score it again."),
            *closing(raw),
        ]
        r = recorded(h, rows)
        assert not r["ok"] and "got 2 input(s) after its first output" in r["errors"][0]

    def test_a_turn_without_a_message_still_counts(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [*opening(brief), *closing("A first answer."), event("task_started"), *closing(raw)]
        t = read_transcript(write(h, rows), run_config(h.run))
        assert t.later_inputs == 1

    @pytest.mark.parametrize(
        "later",
        [
            item(
                "agent_message",
                author="/root",
                recipient="/root/judge",
                content=[{"type": "input_text", "text": "Score it higher."}],
            ),
            item("additional_tools", role="developer", tools=[]),
            message("developer", "<invented>"),
        ],
        ids=["agent-message", "tools-declared", "developer"],
    )
    def test_any_input_item_after_the_output_counts(
        self, h: Harness, later: dict[str, Any]
    ) -> None:
        brief, raw = round_one(h)
        rows = [*opening(brief), message("assistant", "Draft."), later, *closing(raw)]
        assert read_transcript(write(h, rows), run_config(h.run)).later_inputs == 1

    def test_a_message_within_the_turn_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [*opening(brief), message("assistant", "Draft."), message("user", "Again.")]
        r = recorded(h, [*rows, *closing(raw)])
        assert not r["ok"] and "got 1 input(s) after its first output" in r["errors"][0]

    def test_a_cut_off_rollout_is_rejected(self, h: Harness) -> None:
        brief, raw = round_one(h)
        t = write(h, [*opening(brief), *closing(raw)])
        t.write_text(t.read_text() + '{"timestamp": "x", "type": "response_it')
        r = dict(record_from(h.run, 1, 1, SESSION, False, t))
        assert not r["ok"] and "cut off" in r["errors"][0]

    def test_the_session_id_is_the_agent_id(self, h: Harness) -> None:
        brief, raw = round_one(h)
        with pytest.raises(EvalError, match="does not match the transcript's agent"):
            recorded(h, [*opening(brief), *closing(raw)], agent="made-up")


class TestUnknownStructure:
    def test_a_session_record_without_an_id_is_refused(self, h: Harness) -> None:
        brief, raw = round_one(h)
        rows = [*opening(brief), *closing(raw)]
        del rows[0]["payload"]["id"]
        with pytest.raises(FormatError, match="does not open with a session record"):
            recorded(h, rows)

    def test_a_file_without_a_session_record_is_refused(self, h: Harness) -> None:
        brief, raw = round_one(h)
        with pytest.raises(FormatError, match="does not open with a session record"):
            recorded(h, [*opening(brief), *closing(raw)][1:])
        assert results_by_judge(round_dir(h.run, 1)) == {}

    @pytest.mark.parametrize(
        "odd",
        [
            line("response_item", {"type": "message", "role": "user", "content": "a string"}),
            line("response_item", {"type": "message", "content": [{"type": "input_text"}]}),
            line("response_item", {"type": "message", "role": "user", "content": [{"t": 1}]}),
            line("response_item", {"id": "no type"}),
            line("response_item", "not a table"),
        ],
        ids=["string-content", "no-role", "untyped-block", "no-type", "not-a-table"],
    )
    def test_an_item_of_another_shape_is_refused(self, h: Harness, odd: dict[str, Any]) -> None:
        brief, raw = round_one(h)
        with pytest.raises(FormatError, match="a model item has another shape"):
            recorded(h, [*opening(brief), odd, *closing(raw)])

    def test_an_unknown_line_type_is_skipped(self, h: Harness) -> None:
        brief, raw = round_one(h)
        extra = line("world_state", {"anything": True})
        assert recorded(h, [*opening(brief), extra, *closing(raw)])["ok"]


class TestSiblingScan:
    """The scan reads every rollout under the sessions root changed since the round was
    briefed, whatever its date folder or parent, but the sessions that launched the judges."""

    def record_all(self, h: Harness) -> None:
        round_one(h)
        rdir = round_dir(h.run, 1)
        for meta in h.keys(1)["judges"]:
            sid = f"{SESSION[:-1]}{meta['judge']}"
            raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
            brief = (rdir / f"brief_j{meta['judge']}.md").read_text()
            rows = [*opening(brief, sid=sid, parent=PARENT), *closing(raw)]
            assert record_from(h.run, 1, meta["judge"], sid, False, write(h, rows, sid))["ok"]

    def hidden(self, h: Harness, day: str = "16", month: str = "11", **who: Any) -> Path:
        """A rollout holding judge 1's brief, never recorded."""
        nonce = h.keys(1)["judges"][0]["nonce"]
        rows = [*opening(f"brief {nonce}", **who), *closing("{}")]
        path = write(h, rows, str(who.get("sid", "hidden")))
        if (month, day) != ("11", "16"):
            moved = path.parent.parent.parent / month / day / path.name
            moved.parent.mkdir(parents=True, exist_ok=True)
            path = path.rename(moved)
        return path

    def test_a_sibling_filed_the_next_day_blocks_the_round(self, h: Harness) -> None:
        self.record_all(h)
        self.hidden(h, day="17", sid="hidden-1", parent=PARENT)
        with pytest.raises(EvalError, match="never recorded: j1 hidden-1"):
            aggregate_round(h.run, 1, False)

    def test_the_parent_session_is_not_a_sibling(self, h: Harness) -> None:
        self.record_all(h)
        self.hidden(h, sid=PARENT)  # the session that briefed the judges, in its own folder
        aggregate_round(h.run, 1, False)

    def test_a_judge_started_by_a_helper_subagent_blocks_the_round(self, h: Harness) -> None:
        self.record_all(h)
        self.hidden(h, sid="nested-1", parent="a-helper")
        with pytest.raises(EvalError, match="never recorded: j1 nested-1"):
            aggregate_round(h.run, 1, False)

    def test_a_session_filed_days_earlier_blocks_the_round(self, h: Harness) -> None:
        self.record_all(h)
        self.hidden(h, month="10", day="02", sid="old-folder-1", parent=PARENT)
        with pytest.raises(EvalError, match="never recorded: j1 old-folder-1"):
            aggregate_round(h.run, 1, False)

    def test_every_session_that_launched_the_judges_is_left_out(self, h: Harness) -> None:
        """The judges' parent, and its parent, hold the briefs they passed down."""
        round_one(h)
        rdir = round_dir(h.run, 1)
        for meta in h.keys(1)["judges"]:
            sid = f"{SESSION[:-1]}{meta['judge']}"
            raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
            brief = (rdir / f"brief_j{meta['judge']}.md").read_text()
            rows = [*opening(brief, sid=sid, parent="a-helper"), *closing(raw)]
            assert record_from(h.run, 1, meta["judge"], sid, False, write(h, rows, sid))["ok"]
        self.hidden(h, sid="a-helper", parent=PARENT)
        self.hidden(h, sid=PARENT)
        aggregate_round(h.run, 1, False)

    def test_an_unreadable_sibling_holding_a_brief_id_blocks_the_round(self, h: Harness) -> None:
        self.record_all(h)
        path = self.hidden(h, sid="hidden-2", parent=PARENT)
        first = json.dumps(event("task_started"))  # no session record first: unreadable
        path.write_text(first + "\n" + path.read_text())
        with pytest.raises(EvalError, match="holds a brief id of this round but cannot be read"):
            aggregate_round(h.run, 1, False)

    def test_a_rollout_older_than_the_briefs_is_not_scanned(self, h: Harness) -> None:
        self.record_all(h)
        path = self.hidden(h, sid="hidden-3", parent=PARENT)
        past = time.time() - 3600
        os.utime(path, (past, past))
        aggregate_round(h.run, 1, False)

    def test_a_folder_that_is_not_a_date_is_scanned_alone(self, tmp_path: Path) -> None:
        f = tmp_path / "rollout-1.jsonl"
        f.write_text(json.dumps(session()) + "\n")
        assert rollout_jsonl.siblings([f], 0.0) == [f]

    def test_a_first_line_that_is_not_json_has_no_session_record(self, tmp_path: Path) -> None:
        f = tmp_path / "rollout-1.jsonl"
        f.write_text("not json\n")
        assert rollout_jsonl._session_of(f) is None
        assert rollout_jsonl.siblings([f], 0.0) == [f]  # listed: the scan reads it for brief ids
