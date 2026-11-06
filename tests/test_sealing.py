"""The sealed panel: only after the loop stops, once per run, never feeding a revision."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import BAD, FLAT, Harness
from sealed_panel.aggregate import aggregate_round
from sealed_panel.errors import EvalError
from sealed_panel.record import record_reply
from sealed_panel.report import status


class TestSealing:
    def test_one_shot_seal_needs_no_loop(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        assert status(h.run)["decision"] == "SEALED"

    def test_seal_refused_while_the_loop_continues(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 3, "evidence": 3})
        assert h.decision()["decision"] == "CONTINUE"
        with pytest.raises(EvalError):
            h.brief(1, "cand", sealed=True)

    def test_second_sealed_panel_on_the_same_text_refused(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        with pytest.raises(EvalError):
            h.brief(2, "cand", sealed=True)

    def test_no_loop_round_after_a_stop(self, h: Harness) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT)
        assert h.decision()["decision"] == "CAP"
        with pytest.raises(EvalError):
            h.brief(2, "v2")

    def test_no_loop_round_after_seal(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        with pytest.raises(EvalError):
            h.brief(1, "v2")

    def test_one_shot_cannot_be_resealed_on_a_retouched_text(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        with pytest.raises(EvalError):
            h.brief(2, "v2", sealed=True)

    def test_loop_judge_cannot_sit_on_the_sealed_panel(self, h: Harness) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT)
        h.brief(1, "v2", sealed=True)
        with pytest.raises(EvalError):
            record_reply(h.run, 1, 1, "agent1", "{}", True)

    def test_no_new_round_after_converged(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 5, "evidence": 5})
        with pytest.raises(EvalError):
            h.brief(2, "v2")

    def test_sealed_panel_carries_the_first_version_as_baseline(self, h: Harness) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT)
        h.brief(1, "v2", sealed=True)
        assert "baseline" in h.keys(1, sealed=True)["roles"]

    def test_one_agent_cannot_take_two_sealed_seats(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand", sealed=True)
        h.answer(1, {"candidate": FLAT, "control": BAD}, sealed=True, judges=1)
        with pytest.raises(EvalError):
            record_reply(h.run, 1, 2, "agent1", "{} other", True)

    def test_unaggregated_seal_blocks_a_second_seal(self, h: Harness) -> None:
        h.init()
        h.brief(1, "cand", sealed=True)  # briefed, never aggregated
        with pytest.raises(EvalError):
            h.brief(2, "v2", sealed=True)

    def test_sealed_panel_needs_its_measurements(self, h: Harness) -> None:
        h.init(measured=[{"id": "timing", "description": "75 s or less", "max": 75}])
        h.brief(1, "cand", sealed=True)
        h.answer(1, {"candidate": FLAT, "control": BAD}, sealed=True)
        with pytest.raises(EvalError, match="measure"):
            aggregate_round(h.run, 1, True)


class TestRunFiles:
    def test_init_refuses_a_directory_holding_other_files(self, h: Harness) -> None:
        h.run.mkdir()
        (h.run / "config.json").write_text('{"app": "precious"}')
        with pytest.raises(EvalError, match="not empty and is not a sealed-panel run"):
            h.init()
        assert (h.run / "config.json").read_text() == '{"app": "precious"}'

    def test_init_takes_a_new_or_empty_directory(self, h: Harness) -> None:
        h.run.mkdir()
        h.init()

    def test_a_planted_link_stops_a_brief(self, h: Harness, tmp_path: Path) -> None:
        h.init()
        rdir = h.run / "rounds" / "r1"
        rdir.mkdir(parents=True)
        outside = tmp_path / "outside.md"
        (rdir / "candidate.md").symlink_to(outside)
        with pytest.raises(EvalError, match="symbolic link"):
            h.brief(1, "cand")
        assert not outside.exists()

    def test_a_planted_link_stops_the_findings(self, h: Harness, tmp_path: Path) -> None:
        h.init(judges_per_persona=2)
        outside = tmp_path / "outside"
        outside.mkdir()
        (h.run / "feedback").symlink_to(outside)
        with pytest.raises(EvalError, match="symbolic link"):
            h.full(1, "cand", FLAT)
        assert list(outside.iterdir()) == []
