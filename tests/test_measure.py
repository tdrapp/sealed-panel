"""Instruments: a numeric threshold decides, and a measurement is taken once."""

from __future__ import annotations

import pytest

from conftest import Harness
from sealed_panel.errors import EvalError
from sealed_panel.record import record_measurement
from sealed_panel.store import load_json, round_dir

TIMING = [{"id": "timing", "description": "75 s or less", "max": 75}]


class TestMeasure:
    def test_numeric_threshold_decided_by_script(self, h: Harness) -> None:
        h.init(measured=TIMING)
        with pytest.raises(EvalError):  # a round is briefed before it is measured
            record_measurement(h.run, 1, "timing", "60", None, False)
        h.brief(1, "cand")
        with pytest.raises(EvalError):
            record_measurement(h.run, 1, "timing", "81", True, False)
        with pytest.raises(EvalError):
            record_measurement(h.run, 1, "timing", "fast", None, False)
        assert not record_measurement(h.run, 1, "timing", "81", None, False)
        with pytest.raises(EvalError):  # 75 then 58 would buy a pass
            record_measurement(h.run, 1, "timing", "58", None, False)
        assert load_json(round_dir(h.run, 1) / "measured.json")[0]["value"] == "81"

    def test_under_the_threshold_passes(self, h: Harness) -> None:
        h.init(measured=TIMING)
        h.brief(1, "cand")
        assert record_measurement(h.run, 1, "timing", "60.8", None, False)

    def test_unknown_measure_refused(self, h: Harness) -> None:
        h.init(measured=TIMING)
        h.brief(1, "cand")
        with pytest.raises(EvalError):
            record_measurement(h.run, 1, "length", "10", None, False)
