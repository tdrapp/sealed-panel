"""Recording a judge: calibration, quotes, severity caps, and every way to re-roll a reply."""

from __future__ import annotations

import json
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, strip_fence
from sealed_panel.aggregate import aggregate_round
from sealed_panel.config import default_config
from sealed_panel.errors import EvalError
from sealed_panel.record import check_output, record_reply
from sealed_panel.store import round_dir

SOURCE = {"candidate": "Fraud fell after the scored queue went live in Lyon."}
GOOD_QUOTE = "the scored queue went live"
BAD_QUOTE = "nothing like the source at all"


def cell(quote: str = GOOD_QUOTE, severity: str = "none", score: int = 4) -> dict[str, Any]:
    return {"reasoning": "r", "quote": quote, "severity": severity, "score": score}


@pytest.fixture
def ready(h: Harness) -> Harness:
    h.init()
    return h


class TestRecord:
    def test_valid_judge(self, ready: Harness) -> None:
        rs = ready.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=1)
        assert rs[0]["ok"], rs[0]["errors"]

    def test_control_above_two_discards_judge(self, ready: Harness) -> None:
        rs = ready.play(
            1, "cand", {"candidate": FLAT, "control": {"clarity": 3, "evidence": 3}}, judges=1
        )
        assert not rs[0]["ok"]
        assert "control scored" in rs[0]["errors"][-1]

    def test_known_good_below_four_discards_judge(self, ready: Harness) -> None:
        rs = ready.play(
            1,
            "cand",
            {"candidate": FLAT, "control": BAD, "known_good": {"clarity": 3, "evidence": 3}},
            judges=1,
            good=True,
        )
        assert not rs[0]["ok"]
        assert "known-good" in rs[0]["errors"][-1]

    def test_fabricated_quotes_discard_judge(self, ready: Harness) -> None:
        rs = ready.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=1, bad_quote=True)
        assert not rs[0]["ok"]

    def test_severity_caps_each_dimension(self, ready: Harness) -> None:
        rs = ready.play(
            1,
            "cand",
            {"candidate": {"clarity": 5, "evidence": 5}, "control": BAD},
            judges=1,
            severity={"evidence": "material", "clarity": "blocking"},
        )
        sc = rs[0]["items"]["candidate"]["scores"]
        assert (sc["evidence"], sc["clarity"]) == (4, 3)

    def test_missing_severity_rejected(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        meta = ready.keys(1)["judges"][0]
        raw = strip_fence(
            judge_json(meta, ready.texts(1, meta), {"candidate": FLAT, "control": BAD})
        )
        for it in raw["items"].values():
            del it["dimensions"]["clarity"]["severity"]
        r = record_reply(ready.run, 1, meta["judge"], "nosev", json.dumps(raw), False)
        assert not r["ok"]

    def test_reused_agent_refused_across_rounds(self, ready: Harness) -> None:
        ready.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=1)
        with pytest.raises(EvalError):
            record_reply(ready.run, 1, 2, "agent1", "{}", False)

    def test_second_valid_reply_for_a_judge_is_a_reroll(self, ready: Harness) -> None:
        ready.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=1)
        with pytest.raises(EvalError):
            ready.answer(1, {"candidate": {"clarity": 5, "evidence": 5}, "control": BAD}, judges=1)

    def test_replacement_allowed_after_reject_then_capped(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        for i in range(default_config().max_replacements + 1):
            r = record_reply(ready.run, 1, 1, f"bad{i}", f"no json {i}", False)
            assert not r["ok"]
        with pytest.raises(EvalError):
            record_reply(ready.run, 1, 1, "bad-last", "no json last", False)

    def test_malformed_and_out_of_range_rejected(self, ready: Harness) -> None:
        rs = ready.play(
            1, "cand", {"candidate": {"clarity": 6, "evidence": 4}, "control": BAD}, judges=1
        )
        assert not rs[0]["ok"]
        r = record_reply(ready.run, 1, 2, "x2", '{"items": {"Q": {"dimensions": {}}}}', False)
        assert not r["ok"]


class TestRerollRefusals:
    def test_no_late_judge_and_no_second_aggregate(self, h: Harness) -> None:
        h.init(judges_per_persona=4)
        h.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=3)
        record_reply(h.run, 1, 4, "j4-first", "no json j4", False)  # slot 4 attempted, rejected
        aggregate_round(h.run, 1, False)
        meta = h.keys(1)["judges"][3]  # judge 4, never recorded
        late = judge_json(
            meta, h.texts(1, meta), {"candidate": {"clarity": 5, "evidence": 5}, "control": BAD}
        )
        with pytest.raises(EvalError, match="already aggregated"):
            record_reply(h.run, 1, 4, "late-judge", late, False)
        with pytest.raises(EvalError):
            aggregate_round(h.run, 1, False)

    def test_same_reply_under_another_agent_id_refused(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        meta = ready.keys(1)["judges"][0]
        raw = judge_json(meta, ready.texts(1, meta), {"candidate": FLAT, "control": BAD})
        record_reply(ready.run, 1, 1, "aONE", raw, False)
        with pytest.raises(EvalError):
            record_reply(ready.run, 1, 2, "aONE-again", raw, False)

    def test_empty_agent_id_refused(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        with pytest.raises(EvalError):
            record_reply(ready.run, 1, 1, "  ", "{}", False)

    def test_control_check_uses_scores_even_with_a_bad_quote(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        meta = ready.keys(1)["judges"][0]
        raw = strip_fence(
            judge_json(
                meta,
                ready.texts(1, meta),
                {"candidate": FLAT, "control": {"clarity": 5, "evidence": 2}},
            )
        )
        ctrl_label = next(lbl for lbl, role in meta["key"].items() if role == "control")
        raw["items"][ctrl_label]["dimensions"]["clarity"]["quote"] = (
            "words that are not in the control"
        )
        r = record_reply(ready.run, 1, meta["judge"], "q1", json.dumps(raw), False)
        assert not r["ok"], "a 5 on the control with a fake quote must still count"

    def test_agent_ids_that_look_alike_do_not_share_a_file(self, ready: Harness) -> None:
        ready.brief(1, "cand")
        for aid in ("a/b", "a_b", "a:b"):
            record_reply(ready.run, 1, 1, aid, f"no json {aid}", False)
        assert len(list(round_dir(ready.run, 1).glob("out_j1_*.json"))) == 3
        with pytest.raises(EvalError):
            record_reply(ready.run, 1, 1, "a b", "no json 4", False)


class TestCheckOutput:
    def test_one_third_of_bad_quotes_is_the_line(self) -> None:
        out = {"items": {"A": {"dimensions": {"a": cell(), "b": cell(), "c": cell(BAD_QUOTE)}}}}
        r = check_output(out, {"A": "candidate"}, SOURCE, ["a", "b", "c"])
        assert r["ok"], "1 bad quote in 3 is tolerated"
        out["items"]["A"]["dimensions"]["b"]["quote"] = "also not in the source text"
        assert not check_output(out, {"A": "candidate"}, SOURCE, ["a", "b", "c"])["ok"]

    def test_two_bad_quotes_in_five_discard_the_judge(self) -> None:
        dims = {k: cell() for k in "abcde"}
        dims["d"]["quote"] = dims["e"]["quote"] = BAD_QUOTE
        out = {"items": {"A": {"dimensions": dims}}}
        assert not check_output(out, {"A": "candidate"}, SOURCE, list("abcde"))["ok"]

    def test_cosmetic_severity_keeps_a_five(self) -> None:
        out = {"items": {"A": {"dimensions": {"a": cell(severity="cosmetic", score=5)}}}}
        r = check_output(out, {"A": "candidate"}, SOURCE, ["a"])
        assert r["items"]["candidate"]["scores"]["a"] == 5


class TestKnownGood:
    def test_known_good_checked_on_raw_scores(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand", good=True)
        meta = h.keys(1)["judges"][0]
        scores = {"candidate": FLAT, "control": BAD, "known_good": {"clarity": 5, "evidence": 1}}
        raw = strip_fence(judge_json(meta, h.texts(1, meta), scores))
        good_label = next(lbl for lbl, role in meta["key"].items() if role == "known_good")
        raw["items"][good_label]["dimensions"]["evidence"]["quote"] = "words that are not in it"
        r = record_reply(h.run, 1, meta["judge"], "kg1", json.dumps(raw), False)
        assert not r["ok"], "a 1 on the known-good behind a fake quote still counts"
