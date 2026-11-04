"""Learning: rubric-level candidates written to the inbox after a sealed panel."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec, strip_fence
from sealed_panel import learn, library
from sealed_panel.aggregate import aggregate_round
from sealed_panel.cli import main
from sealed_panel.config import load_config
from sealed_panel.errors import EvalError
from sealed_panel.learn import proposals_after_seal, user_disagreement
from sealed_panel.record import record_reply
from sealed_panel.spec import dims_for, init_run

FIVE = {"clarity": 5, "evidence": 5}
# A control that stays calibrated (mean 2) but scores clarity close to a strong candidate.
CLOSE = {"clarity": 3, "evidence": 1}


def close_seal(h: Harness) -> dict[str, Any]:
    """A sealed panel where the strong candidate (4) and the control (3) sit close on clarity."""
    h.play(1, "cand", {"candidate": FLAT, "control": CLOSE}, sealed=True)
    return aggregate_round(h.run, 1, True)


def kinds(lib: Path) -> list[tuple[str, str, int]]:
    rows, _ = library.open_candidates(lib)
    return sorted((c["kind"], c["dimension"], c["count"]) for c in rows)


def seal_with(
    h: Harness, per_judge: list[dict[str, Any]], control: dict[str, int] | None = None
) -> dict[str, Any]:
    """Brief a sealed panel and record each judge with its own candidate scores and options."""
    h.brief(1, "cand", sealed=True)
    for meta, kw in zip(h.keys(1, True)["judges"], per_judge, strict=True):
        kw = dict(kw)
        cand = kw.pop("scores")
        sc = {r: (cand if r == "candidate" else control or BAD) for r in meta["key"].values()}
        raw = judge_json(meta, h.texts(1, meta, True), sc, **kw)
        record_reply(h.run, 1, meta["judge"], f"s{meta['judge']}", raw, True)
    return aggregate_round(h.run, 1, True)


class TestJudgesDisagree:
    def test_a_spread_of_two_is_flagged(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(
            h, [{"scores": {"clarity": 3, "evidence": 4}}, {"scores": FLAT}, {"scores": FIVE}]
        )
        assert kinds(private_library) == [("judges_disagree", "clarity", 1)]
        c = library.open_candidates(private_library)[0][0]
        ev = c["evidence"][0]
        assert ev["scores"] == [3, 4, 5]
        assert ev["run_dir"] == str(h.run.absolute())
        assert {q["judge"] for q in ev["quotes"]} == {1, 2, 3}
        assert all(q["verified"] for q in ev["quotes"])

    def test_a_spread_of_one_is_not(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(h, [{"scores": FLAT}, {"scores": FLAT}, {"scores": FIVE}])
        assert kinds(private_library) == []

    def test_a_blocking_four_contradicts(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(h, [{"scores": FLAT, "severity": {"evidence": "blocking"}},
                      {"scores": FLAT}, {"scores": FLAT}])  # fmt: skip
        assert kinds(private_library) == [("judges_disagree", "evidence", 1)]
        note = library.open_candidates(private_library)[0][0]["evidence"][0]["note"]
        assert "judge 1 gave 4 with severity blocking" in note

    def test_a_none_with_a_one_contradicts(self, h: Harness, private_library: Path) -> None:
        h.init()
        low = {"clarity": 1, "evidence": 4}
        seal_with(h, [{"scores": low}, {"scores": {"clarity": 2, "evidence": 4}},
                      {"scores": {"clarity": 2, "evidence": 4}}], control=BAD)  # fmt: skip
        found = kinds(private_library)
        assert ("judges_disagree", "clarity", 1) in found

    def test_a_material_five_contradicts(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(
            h,
            [{"scores": FIVE, "severity": {"evidence": "material"}}, {"scores": FLAT},
             {"scores": FLAT}],
        )  # fmt: skip
        assert ("judges_disagree", "evidence", 1) in kinds(private_library)

    def test_a_material_four_is_consistent(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(h, [{"scores": FLAT, "severity": {"evidence": "material"}},
                      {"scores": FLAT}, {"scores": FLAT}])  # fmt: skip
        assert kinds(private_library) == []

    def test_loop_rounds_do_not_count(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.brief(1, "cand")
        for n, (meta, sc) in enumerate(zip(h.keys(1)["judges"], [{"clarity": 2, "evidence": 4},
                                       FLAT, FIVE], strict=True)):  # fmt: skip
            raw = judge_json(meta, h.texts(1, meta), {"candidate": sc, "control": BAD})
            record_reply(h.run, 1, meta["judge"], f"l{n}", raw, False)
        aggregate_round(h.run, 1, False)
        h.full(1, "cand", FLAT, sealed=True)
        assert kinds(private_library) == []


class TestControlTooClose:
    def test_a_strong_candidate_close_to_the_control(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        e = close_seal(h)
        assert e["personas"]["coo"]["dimensions"]["clarity"]["control_median"] == 3
        assert kinds(private_library) == [("control_too_close", "clarity", 1)]
        ev = library.open_candidates(private_library)[0][0]["evidence"][0]
        assert (ev["candidate_raw_mean"], ev["control_raw_mean"]) == (4, 3)

    def test_calibration_counts_the_persona_dimensions(self, h: Harness) -> None:
        """The reviewer's probe: a 3-dimension rubric, a persona limited to 2 of them. A
        control at 4 and 1 has a mean of 2.5 over the persona's dimensions, and is refused."""
        spec = make_spec()
        spec["personas"].append({"id": "cfo", "description": "CFO of a retail bank"})
        spec["dimensions"].append(
            {"id": "cost", "question": "Is the cost clear?", "personas": ["cfo"],
             "anchors": {"1": "no cost", "3": "a range", "5": "a firm figure"}}
        )  # fmt: skip
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(spec))
        init_run(h.run, sp)
        h.brief(1, "cand", sealed=True)
        results = {}
        for meta in h.keys(1, True)["judges"]:
            dims = [d["id"] for d in dims_for(spec, meta["persona"])]
            control = {d: 4 if d == "clarity" else 1 for d in dims}
            sc = {r: control if r == "control" else dict.fromkeys(dims, 4)
                  for r in meta["key"].values()}  # fmt: skip
            raw = judge_json(meta, h.texts(1, meta, True), sc)
            results[meta["judge"]] = (meta["persona"], record_reply(
                h.run, 1, meta["judge"], f"p{meta['judge']}", raw, True))  # fmt: skip
        coo = [r for p, r in results.values() if p == "coo"]
        cfo = [r for p, r in results.values() if p == "cfo"]
        assert coo and all(not r["ok"] and r["control_mean"] == 2.5 for r in coo)
        assert cfo and all(r["ok"] and r["control_mean"] == 2.0 for r in cfo)

    def test_a_weak_candidate_never_triggers_it(self, h: Harness, private_library: Path) -> None:
        h.init()  # the reviewer's probe: candidate 3/5, control 2/1, three unanimous judges
        h.play(1, "cand", {"candidate": {"clarity": 3, "evidence": 5},
                           "control": {"clarity": 2, "evidence": 1}}, sealed=True)  # fmt: skip
        aggregate_round(h.run, 1, True)
        assert kinds(private_library) == []

    def test_the_threshold_is_a_setting(self, h: Harness, private_library: Path) -> None:
        cfg = h.tmp / "c.toml"
        cfg.write_text("[library]\ncontrol_min_candidate = 4.5\n")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg))
        close_seal(h)
        assert kinds(private_library) == []

    def test_the_gap_is_a_setting(self, h: Harness, private_library: Path) -> None:
        cfg = h.tmp / "c.toml"
        cfg.write_text("[library]\ncontrol_gap = 0.5\n")  # 4 vs 3 is a gap of 1: not close
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg))
        close_seal(h)
        assert kinds(private_library) == []

    def test_weak_loop_drafts_do_not_count(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=2)
        drafts = {"clarity": 2, "evidence": 2}
        h.full(1, "cand", drafts)
        h.full(2, "v2", drafts, drafts)
        h.full(1, "v2", FIVE, sealed=True)
        assert kinds(private_library) == []

    def test_raw_scores_are_compared_with_raw_scores(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        h.brief(1, "cand", sealed=True)
        for meta in h.keys(1, True)["judges"]:
            sc = {"candidate": FLAT, "control": CLOSE}
            raw = judge_json(meta, h.texts(1, meta, True), sc, severity={"clarity": "blocking"})
            record_reply(h.run, 1, meta["judge"], f"b{meta['judge']}", raw, True)
        e = aggregate_round(h.run, 1, True)
        assert e["personas"]["coo"]["dimensions"]["clarity"]["median"] == 3  # capped
        # Raw against raw: 4 against 3 is close; the capped 3 would fall under the 4 floor.
        assert sorted(k for k, _, _ in kinds(private_library)) == [
            "control_too_close",
            "judges_disagree",
        ]

    def test_one_loop_round_is_not_enough(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", {"clarity": 2, "evidence": 4})
        h.full(1, "cand", FLAT, sealed=True)
        assert kinds(private_library) == []

    def test_a_gap_of_two_is_fine(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 3, "evidence": 4}, sealed=True)
        assert kinds(private_library) == []


class TestUserDisagreement:
    def test_needs_a_sealed_run(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 3, "evidence": 3})
        with pytest.raises(EvalError, match="seal the run first"):
            user_disagreement(h.run, "too kind")

    def test_quotes_come_from_the_sealed_round(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT)
        h.full(1, "v2", FLAT, sealed=True)
        user_disagreement(h.run, "the evidence reads thinner than 4", "evidence")
        c = library.open_candidates(private_library)[0][0]
        ev = c["evidence"][0]
        v2 = h.files["v2"].read_text()[:40]
        assert {q["quote"] for q in ev["quotes"]} == {v2}
        assert ev["sealed_median"] == 4
        assert ev["run_dir"] == str(h.run.absolute())

    def test_an_unverified_quote_is_labelled(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.brief(1, "cand", sealed=True)
        for meta in h.keys(1, True)["judges"]:
            scores = {"candidate": FLAT, "control": BAD}
            raw = strip_fence(judge_json(meta, h.texts(1, meta, True), scores))
            if meta["judge"] == 1:
                label = next(k for k, v in meta["key"].items() if v == "candidate")
                raw["items"][label]["dimensions"]["evidence"]["quote"] = "words that are not there"
            record_reply(h.run, 1, meta["judge"], f"q{meta['judge']}", json.dumps(raw), True)
        aggregate_round(h.run, 1, True)
        user_disagreement(h.run, "thin", "evidence")
        quotes = library.open_candidates(private_library)[0][0]["evidence"][0]["quotes"]
        assert {q["judge"]: q["verified"] for q in quotes} == {1: False, 2: True, 3: True}

    def test_with_and_without_a_dimension(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        with pytest.raises(EvalError, match="no dimension"):
            user_disagreement(h.run, "x", "ghost")
        with pytest.raises(EvalError, match="--note"):
            user_disagreement(h.run, "  ")
        user_disagreement(h.run, "the evidence reads thin", "evidence")
        user_disagreement(h.run, "it misses the tone")
        assert kinds(private_library) == [("user_disagreement", "(none)", 1),
                                          ("user_disagreement", "evidence", 1)]  # fmt: skip

    def test_the_same_disagreement_is_counted_once_per_note(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        user_disagreement(h.run, "first read", "evidence")
        user_disagreement(h.run, "second read", "Evidence".lower())
        assert kinds(private_library) == [("user_disagreement", "evidence", 2)]

    def test_a_rejected_disagreement_is_not_reopened(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        cid = user_disagreement(h.run, "first read", "evidence").stem
        library.reject(private_library, cid, "the anchor is right")
        with pytest.raises(EvalError, match="rejected before"):
            user_disagreement(h.run, "again", "evidence")

    def test_feedback_command(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        assert main(["feedback", str(h.run), "--note", "too kind", "--dimension", "clarity"]) == 0
        assert "recorded your read as candidate" in capsys.readouterr().out


class TestGuards:
    def test_learning_never_writes_a_template(self, h: Harness, private_library: Path) -> None:
        h.init()
        seal_with(
            h,
            [
                {"scores": {"clarity": 4, "evidence": 2}},
                {"scores": FLAT},
                {"scores": FLAT},
            ],
            control=CLOSE,
        )
        assert {k for k, _, _ in kinds(private_library)} == {"judges_disagree", "control_too_close"}
        assert sorted(p.name for p in private_library.iterdir()) == ["inbox"]

    def test_learning_can_be_switched_off(self, h: Harness, private_library: Path) -> None:
        cfg = h.tmp / "c.toml"
        cfg.write_text("[library]\nlearn = false\n")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg))
        e = close_seal(h)
        assert (e["inbox"], e["learning"]) == ([], "learning off")
        assert not private_library.exists()

    def test_learning_never_fails_a_seal(
        self, h: Harness, private_library: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:  # fmt: skip
        h.init()

        def broken(run: Path) -> list[dict[str, Any]]:
            raise OSError("disk full")

        monkeypatch.setattr(learn, "proposals_after_seal", broken)
        e = close_seal(h)
        assert e["learning"].startswith("learning skipped: disk full")
        assert main(["learn", str(h.run)]) == 2
        monkeypatch.undo()
        assert main(["learn", str(h.run)]) == 0
        assert "1 rubric candidate(s) in" in capsys.readouterr().out
        assert kinds(private_library) == [("control_too_close", "clarity", 1)]

    def test_learning_twice_adds_nothing(self, h: Harness, private_library: Path) -> None:
        h.init()
        close_seal(h)
        assert main(["learn", str(h.run)]) == 0
        assert kinds(private_library) == [("control_too_close", "clarity", 1)]

    def test_learn_needs_a_seal(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 3, "evidence": 3})
        assert main(["learn", str(h.run)]) == 2
        with pytest.raises(EvalError, match="seal the run first"):
            proposals_after_seal(h.run)

    def test_aggregate_prints_the_library(self, h: Harness, private_library: Path,
                                          capsys: pytest.CaptureFixture[str]) -> None:  # fmt: skip
        h.init()
        h.play(1, "cand", {"candidate": FLAT, "control": CLOSE}, sealed=True)
        assert main(["aggregate", str(h.run), "--sealed"]) == 0
        assert f"1 rubric candidate(s) in {private_library / 'inbox'}" in capsys.readouterr().out


class TestRunSettings:
    def test_contradiction_follows_the_run_caps(self, h: Harness, private_library: Path) -> None:
        cfg = h.tmp / "c.toml"
        cfg.write_text("[severity.caps]\nblocking = 2\n")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg))
        three = {"clarity": 3, "evidence": 4}
        seal_with(h, [{"scores": three, "severity": {"clarity": "blocking"}},
                      {"scores": three}, {"scores": three}])  # fmt: skip
        note = library.open_candidates(private_library)[0][0]["evidence"][0]["note"]
        assert "judge 1 gave 3 with severity blocking" in note

    def test_feedback_after_a_promotion_says_promoted(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        path = user_disagreement(h.run, "first read", "evidence")
        c = json.loads(path.read_text())
        c["proposed_change"]["anchors"] = {"3": "a three", "5": "a five"}
        path.write_text(json.dumps(c))
        library.promote(private_library, path.stem, into="message")
        with pytest.raises(EvalError, match="already promoted"):
            user_disagreement(h.run, "again", "evidence")
