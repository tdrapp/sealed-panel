"""Instruments: a numeric threshold decides, and a measurement is taken once."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from conftest import Harness
from sealed_panel import record
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

    def test_two_readings_at_once_are_both_kept(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The first reading stops before it writes, run lock held; the second waits for the
        lock, so it never reads the round's readings before the first is written."""
        h.init(measured=[*TIMING, {"id": "length", "description": "200 words or less", "max": 200}])
        h.brief(1, "cand")
        entered, second_in, go = threading.Event(), threading.Event(), threading.Event()
        real = record.dump_json

        def slow_first(path: Any, data: Any) -> None:
            if not entered.is_set():
                entered.set()
                go.wait(30)
            else:
                second_in.set()  # only reachable without the run lock
            real(path, data)

        monkeypatch.setattr(record, "dump_json", slow_first)
        first = threading.Thread(
            target=record_measurement, args=(h.run, 1, "timing", "60", None, False)
        )
        first.start()
        assert entered.wait(30)
        other = threading.Thread(
            target=record_measurement, args=(h.run, 1, "length", "150", None, False)
        )
        other.start()
        try:
            assert not second_in.wait(2), "the second reading read the round's readings meanwhile"
        finally:
            go.set()
            first.join(30)
            other.join(30)
        rows = load_json(round_dir(h.run, 1) / "measured.json")
        assert sorted(r["id"] for r in rows) == ["length", "timing"]
