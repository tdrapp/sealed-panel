"""Void attempts and handback replies: a void clears a block but never fills a seat."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel.aggregate import aggregate_round
from sealed_panel.cli import main
from sealed_panel.config import load_config
from sealed_panel.errors import EvalError, MalformedReply
from sealed_panel.ledger import results_by_judge
from sealed_panel.record import MAX_VOIDS, extract_json, handback_messages, last_reply, record_reply
from sealed_panel.report import render_report, status
from sealed_panel.spec import init_run
from sealed_panel.store import round_dir

TOOL = "ReturnToCaller"


def transcript(
    h: Harness, agent: str, prompt: str, *texts: str, tools: list[Any] | None = None
) -> Path:
    folder = h.tmp / "tasks"
    folder.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = [
        {"type": "user", "agentId": agent, "message": {"content": prompt}}
    ]
    for text in texts:
        rows.append({"type": "assistant", "agentId": agent,
                     "message": {"content": [{"type": "text", "text": text}]}})  # fmt: skip
    for tool_input in tools or []:
        rows.append({"type": "assistant", "agentId": agent, "message": {"content": [
            {"type": "tool_use", "name": TOOL, "input": tool_input}]}})  # fmt: skip
    p = folder / f"{agent}.output"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def brief_text(h: Harness, judge: int, sealed: bool = False) -> str:
    return (round_dir(h.run, 1, sealed) / f"brief_j{judge}.md").read_text()


def record_all(h: Harness, scores: dict[str, Any], sealed: bool = False, skip: int = 0) -> None:
    for meta in h.keys(1, sealed)["judges"]:
        if meta["judge"] == skip:
            continue
        sc = {r: scores.get(r, scores["candidate"]) for r in meta["key"].values()}
        raw = judge_json(meta, h.texts(1, meta, sealed), sc)
        agent = f"ok{int(sealed)}{meta['judge']}"
        t = transcript(h, agent, brief_text(h, meta["judge"], sealed), raw)
        record_reply(h.run, 1, meta["judge"], agent, raw, sealed, "transcript", t)


def void(h: Harness, agent: str, t: Path, judge: int = 1, sealed: bool = False) -> dict[str, Any]:
    return record_reply(h.run, 1, judge, agent, "", sealed, "void", t, void=True)


def init_with_tool(h: Harness) -> None:
    cfg = h.tmp / "c.toml"
    cfg.write_text(f'[transcript]\nhandback_tool = "{TOOL}"\n')
    sp = h.tmp / "spec.json"
    sp.write_text(json.dumps(make_spec()))
    init_run(h.run, sp, load_config(cfg))


class TestSeats:
    def test_voiding_a_running_judge_does_not_fill_its_seat(self, h: Harness) -> None:
        h.init()
        h.play(1, "cand", {"candidate": {"clarity": 5, "evidence": 5}, "control": BAD}, judges=2)
        void(h, "running3", transcript(h, "running3", brief_text(h, 3)), judge=3)
        with pytest.raises(EvalError, match="never fills a seat"):
            aggregate_round(h.run, 1, False)

    def test_three_dead_agents_do_not_lock_a_sealed_seat(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand", sealed=True)
        for i in range(MAX_VOIDS):
            void(h, f"died{i}", transcript(h, f"died{i}", brief_text(h, 1, True)), sealed=True)
        record_all(h, {"candidate": FLAT, "control": BAD}, sealed=True)
        aggregate_round(h.run, 1, True)
        assert status(h.run)["decision"] == "SEALED"

    def test_voids_have_their_own_cap(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        for i in range(MAX_VOIDS):
            void(h, f"died{i}", transcript(h, f"died{i}", brief_text(h, 1)))
        with pytest.raises(EvalError, match="voided attempts"):
            void(h, "died-more", transcript(h, "died-more", brief_text(h, 1)))

    def test_voids_do_not_count_toward_real_attempts(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        for i in range(MAX_VOIDS):
            void(h, f"gone{i}", transcript(h, f"gone{i}", brief_text(h, 1)))
        for i in range(3):  # max_replacements + 1 real attempts are still allowed
            assert not record_reply(h.run, 1, 1, f"bad{i}", f"no json {i}", False)["ok"]
        with pytest.raises(EvalError, match="rejected 3 times"):
            record_reply(h.run, 1, 1, "bad-last", "no json last", False)

    def test_a_run_without_brief_ids_cannot_void(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        kpath = round_dir(h.run, 1) / "keys.json"
        keys = json.loads(kpath.read_text())
        for j in keys["judges"]:
            j.pop("nonce")
        kpath.write_text(json.dumps(keys))
        with pytest.raises(EvalError, match="no brief id"):
            void(h, "old", transcript(h, "old", "anything"))


class TestWhoCanBeVoided:
    def test_a_pasted_judge_with_json_then_prose_cannot_be_voided(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        t = transcript(h, "chatty", brief_text(h, 1), '{"items": {}}', "Done.")
        with pytest.raises(EvalError, match="cannot be voided"):
            void(h, "chatty", t)

    def test_a_pasted_judge_with_a_prose_reply_cannot_be_voided(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        t = transcript(h, "prose", brief_text(h, 1), "I think it is a 4 overall.")
        with pytest.raises(EvalError, match="cannot be voided"):
            void(h, "prose", t)

    def test_a_relay_that_replied_in_json_can_be_voided(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        nonce = h.keys(1)["judges"][0]["nonce"]
        relay = transcript(
            h, "relay2", f"forward brief {nonce} from the run", '{"items": {"Q": {}}}'
        )
        record_all(h, {"candidate": FLAT, "control": BAD})
        r = void(h, "relay2", relay)
        assert r["replied"] is True
        aggregate_round(h.run, 1, False)
        assert "voided after replying (brief not verbatim)" in render_report(h.run)

    def test_voids_and_rejections_are_listed_in_the_report(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        void(h, "deadR", transcript(h, "deadR", brief_text(h, 1)))
        record_reply(h.run, 1, 2, "badR", "no json here", False)
        record_all(h, {"candidate": FLAT, "control": BAD})
        e = aggregate_round(h.run, 1, False)
        assert [v["agent_id"] for v in e["voided"]] == ["deadR"]
        assert [x["judge"] for x in e["rejected_judges"]] == [2]
        rep = render_report(h.run)
        assert "judge 1 attempt deadR voided (no reply)" in rep
        assert "judge 2 rejected: unparseable" in rep


class TestHandback:
    def setup_round(self, h: Harness) -> tuple[str, str]:
        init_with_tool(h)
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        return brief_text(h, 1), judge_json(
            meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD}
        )

    def record_cli(self, h: Harness, agent: str, t: Path) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = main(["record", str(h.run), "--round", "1", "--judge", "1",
                       "--agent-id", agent, "--transcript", str(t)])  # fmt: skip
        return rc, err.getvalue()

    def test_a_handback_only_judge_is_recorded(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        t = transcript(h, "hb1", brief, tools=[{"message": raw}])
        assert last_reply(t, load_config(h.tmp / "c.toml")) == raw
        assert self.record_cli(h, "hb1", t)[0] == 0
        (result,) = results_by_judge(round_dir(h.run, 1))[1]
        assert result["ok"], result["errors"]

    def test_the_handback_wins_over_any_text(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        cfg = load_config(h.tmp / "c.toml")
        assert (
            last_reply(
                transcript(h, "p", brief, "I will score now.", tools=[{"message": raw}]), cfg
            )
            == raw
        )
        retried = transcript(h, "r", brief, tools=[{"message": {"items": {}}}, {"message": raw}])
        assert last_reply(retried, cfg) == raw

    def test_without_the_setting_no_tool_is_read(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        t = transcript(h, "hbX", brief_text(h, 1), tools=[{"message": "x"}])
        with pytest.raises(EvalError, match="no assistant reply"):
            last_reply(t)

    def test_a_malformed_handback_is_never_read_as_a_reply(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        cfg = load_config(h.tmp / "c.toml")
        for agent, tool_input in (("A", {"message": extract_json(raw)}), ("B", {"text": raw}),
                                  ("C", {"message": "  "}), ("E", raw)):  # fmt: skip
            with pytest.raises(MalformedReply, match="malformed"):
                last_reply(transcript(h, agent, brief, tools=[tool_input]), cfg)

    def test_a_malformed_handback_is_recorded_rejected(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        for agent in ("M1", "M2"):
            t = transcript(h, agent, brief, tools=[{"message": extract_json(raw)}])
            assert self.record_cli(h, agent, t)[0] == 0
        results = results_by_judge(round_dir(h.run, 1))[1]
        assert [r["ok"] for r in results] == [False, False]
        assert all("malformed" in r["errors"][0] for r in results)

    def test_a_handback_judge_still_needs_the_brief_and_its_own_id(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        rc, err = self.record_cli(
            h, "made-up", transcript(h, "hb2", brief, tools=[{"message": raw}])
        )
        assert rc == 2 and "does not match the transcript's agent hb2" in err
        rc, err = self.record_cli(
            h, "hb3", transcript(h, "hb3", "score this", tools=[{"message": raw}])
        )
        assert rc == 2 and "does not contain brief j1 verbatim" in err

    def test_a_handback_judge_that_replied_cannot_be_voided(self, h: Harness) -> None:
        brief, raw = self.setup_round(h)
        for agent, tool_input in (("V", {"message": raw}), ("W", {"message": {"items": {}}})):
            with pytest.raises(EvalError, match="cannot be voided"):
                void(h, agent, transcript(h, agent, brief, tools=[tool_input]))


class TestRefusedHandback:
    def rows(self, h: Harness, call_result: dict[str, Any]) -> Path:
        p = h.tmp / "t.jsonl"
        call = {"type": "tool_use", "id": "tu1", "name": TOOL, "input": {"message": "the handback"}}
        result = {"type": "tool_result", "tool_use_id": "tu1", **call_result}
        text = {"type": "text", "text": "the delivered text"}
        rows = [
            {"type": "user", "message": {"content": "brief"}},
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "user", "message": {"content": [result]}},
            {"type": "assistant", "message": {"content": [text]}},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return p

    def test_a_refused_handback_is_not_the_reply(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        p = self.rows(h, {"is_error": True, "content": "not allowed"})
        assert handback_messages(p, cfg) == []
        assert last_reply(p, cfg) == "the delivered text"

    def test_a_delivered_handback_still_wins(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        p = self.rows(h, {"content": "ok"})
        assert last_reply(p, cfg) == "the handback"


class TestPeekedSeat:
    def test_a_peeked_and_voided_seat_refuses_a_pasted_reply(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        nonce = h.keys(1)["judges"][0]["nonce"]
        peek = transcript(h, "peek", f"judge brief {nonce} from its path", '{"items": {"Q": {}}}')
        assert void(h, "peek", peek)["replied"] is True
        with pytest.raises(EvalError, match="never pasted by hand"):
            record_reply(h.run, 1, 1, "pasted", "{} favourable", False, "output")
        meta = h.keys(1)["judges"][0]
        raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
        t = transcript(h, "fresh", brief_text(h, 1), raw)
        assert record_reply(h.run, 1, 1, "fresh", raw, False, "transcript", t)["ok"]

    def test_a_silent_void_still_allows_a_pasted_reply(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand")
        void(h, "dead", transcript(h, "dead", brief_text(h, 1)))
        assert not record_reply(h.run, 1, 1, "pasted", "no json", False, "output")["ok"]


class TestRefusedHandbackEdges:
    def test_an_error_result_without_an_id_skips_no_call(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        p = h.tmp / "noid.jsonl"
        call = {"type": "tool_use", "name": TOOL, "input": {"message": "the handback"}}
        result = {"type": "tool_result", "is_error": True, "content": "some other refusal"}
        text = {"type": "text", "text": "Done."}
        rows = [
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "user", "message": {"content": [result]}},
            {"type": "assistant", "message": {"content": [text]}},
        ]
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        assert last_reply(p, cfg) == "the handback", "delivered: it wins over the later text"

    def test_a_refused_handback_verdict_still_blocks_a_void(self, h: Harness) -> None:
        init_with_tool(h)
        h.brief(1, "cand")
        call = {
            "type": "tool_use",
            "id": "tu9",
            "name": TOOL,
            "input": {"message": '{"items": {"Q": {}}}'},
        }
        result = {
            "type": "tool_result",
            "tool_use_id": "tu9",
            "is_error": True,
            "content": "denied",
        }
        p = h.tmp / "tasks"
        p.mkdir(exist_ok=True)
        t = p / "refusedjudge.output"
        rows = [
            {"type": "user", "agentId": "refusedjudge", "message": {"content": brief_text(h, 1)}},
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "user", "message": {"content": [result]}},
        ]
        t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        with pytest.raises(EvalError, match="cannot be voided"):
            void(h, "refusedjudge", t)


class TestRefusedOnly:
    def refused(self, h: Harness, *messages: Any) -> Path:
        rows: list[dict[str, Any]] = []
        for i, m in enumerate(messages):
            call = {"type": "tool_use", "id": f"r{i}", "name": TOOL, "input": {"message": m}}
            result = {
                "type": "tool_result",
                "tool_use_id": f"r{i}",
                "is_error": True,
                "content": "no",
            }
            rows += [
                {"type": "assistant", "message": {"content": [call]}},
                {"type": "user", "message": {"content": [result]}},
            ]
        p = h.tmp / f"refused{len(messages)}.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return p

    def test_a_refused_only_verdict_is_recorded_from_the_transcript(self, h: Harness) -> None:
        init_with_tool(h)
        h.brief(1, "cand")
        cfg = load_config(h.tmp / "c.toml")
        meta = h.keys(1)["judges"][0]
        verdict = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
        call = {"type": "tool_use", "id": "tuR", "name": TOOL, "input": {"message": verdict}}
        result = {
            "type": "tool_result",
            "tool_use_id": "tuR",
            "is_error": True,
            "content": "refused",
        }
        p = h.tmp / "tasks"
        p.mkdir(exist_ok=True)
        t = p / "refonly.output"
        rows = [
            {"type": "user", "agentId": "refonly", "message": {"content": brief_text(h, 1)}},
            {"type": "assistant", "message": {"content": [call]}},
            {"type": "user", "message": {"content": [result]}},
        ]
        t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        assert last_reply(t, cfg) == verdict
        r = record_reply(h.run, 1, 1, "refonly", last_reply(t, cfg), False, "transcript", t)
        assert r["ok"], r["errors"]

    def test_the_last_of_several_refused_verdicts_is_read(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        assert last_reply(self.refused(h, "first verdict", "last verdict"), cfg) == "last verdict"

    def test_a_malformed_earlier_refused_handback_does_not_hide_the_last(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        assert last_reply(self.refused(h, 42, "the last verdict"), cfg) == "the last verdict"

    def test_a_malformed_last_refused_handback_is_a_malformed_reply(self, h: Harness) -> None:
        init_with_tool(h)
        cfg = load_config(h.tmp / "c.toml")
        with pytest.raises(MalformedReply):
            last_reply(self.refused(h, "an earlier verdict", 42), cfg)
        with pytest.raises(MalformedReply):
            last_reply(self.refused(h, ["not", "text"]), cfg)

    def test_an_empty_tool_setting_reads_no_tool_call(self, h: Harness) -> None:
        h.init()
        p = h.tmp / "blank.jsonl"
        call = {"type": "tool_use", "name": "", "input": {"message": "not a reply"}}
        p.write_text(json.dumps({"type": "assistant", "message": {"content": [call]}}) + "\n")
        with pytest.raises(EvalError, match="no assistant reply"):
            last_reply(p)
