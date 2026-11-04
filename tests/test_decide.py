"""The stop rule: CONVERGED, PLATEAU, CAP or CONTINUE, and what aggregation computes."""

from __future__ import annotations

import pytest

from conftest import BAD, FLAT, Harness
from sealed_panel.aggregate import aggregate_round
from sealed_panel.errors import EvalError
from sealed_panel.record import record_measurement, record_reply

THREE = {"clarity": 3, "evidence": 3}
FIVE = {"clarity": 5, "evidence": 5}


class TestDecide:
    def test_converged(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FIVE)
        assert h.decision()["decision"] == "CONVERGED"

    def test_material_severity_keeps_a_five_from_converging(self, h: Harness) -> None:
        h.init()
        e = h.full(1, "cand", FIVE, severity={"clarity": "material"})
        assert e["personas"]["coo"]["dimensions"]["clarity"]["median"] == 4
        assert h.decision()["decision"] == "CONTINUE"

    def test_plateau(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT)
        h.full(2, "v2", FLAT, FLAT)
        h.full(3, "v3", FLAT, FLAT)
        assert h.decision()["decision"] == "PLATEAU"

    def test_plateau_needs_both_last_gains_low(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", THREE)
        h.full(2, "v2", THREE, THREE)
        h.full(3, "v3", FLAT, THREE)
        assert h.decision()["decision"] == "CONTINUE"

    def test_cap(self, h: Harness) -> None:
        h.init(max_rounds=2)
        h.full(1, "cand", THREE)
        h.full(2, "v2", FLAT, THREE)
        assert h.decision()["decision"] == "CAP"

    def test_continue_on_real_gain(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", THREE)
        h.full(2, "v2", FLAT, THREE)
        d = h.decision()
        assert (d["decision"], d["next_round"]) == ("CONTINUE", 3)

    def test_measured_failure_blocks_convergence(self, h: Harness) -> None:
        h.init(measured=[{"id": "timing", "description": "75 s or less", "max": 75}])
        h.brief(1, "cand")
        record_measurement(h.run, 1, "timing", "81", None, False)
        h.answer(1, {"candidate": FIVE, "control": BAD})
        aggregate_round(h.run, 1, False)
        assert h.decision()["decision"] == "CONTINUE"

    def test_aggregate_needs_two_valid_judges(self, h: Harness) -> None:
        h.init()
        h.play(1, "cand", {"candidate": FLAT, "control": BAD}, judges=1)
        for j in (2, 3):  # attempted, rejected: only one valid judge remains
            record_reply(h.run, 1, j, f"bad-j{j}", f"no json {j}", False)
        with pytest.raises(EvalError, match="valid judge"):
            aggregate_round(h.run, 1, False)


class TestAggregate:
    def test_weights_shape_the_composite(self, h: Harness) -> None:
        h.init()
        e = h.full(1, "cand", {"clarity": 2, "evidence": 5})
        assert e["composite"] == 4.0  # (2*1 + 5*2) / 3

    def test_paired_gain_only_from_round_two(self, h: Harness) -> None:
        h.init()
        e = h.full(1, "cand", FLAT)
        assert e["best_gain"] is None
