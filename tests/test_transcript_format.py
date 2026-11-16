"""The transcript format setting: the removed field names, the closed registry, and a run
frozen before the host adapters, read through the run format's forward rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import BAD, FLAT, Harness, judge_json
from sealed_panel.errors import ConfigError, EvalError
from sealed_panel.hosts import FORMATS
from sealed_panel.record import record_from
from sealed_panel.run import run_config
from sealed_panel.settings import (
    REMOVED_TRANSCRIPT_KEYS,
    TRANSCRIPT_FORMATS,
    config_differences,
    default_config,
    fingerprint,
    load_config,
)
from sealed_panel.store import round_dir


def frozen_before_the_adapters(h: Harness, **changed: str) -> None:
    """A run whose frozen configuration has the transcript field names and no format, the way
    0.4.0 froze it, locked as written."""
    h.init()
    data = json.loads((h.run / "config.json").read_text())
    data["transcript"] = REMOVED_TRANSCRIPT_KEYS | changed
    (h.run / "config.json").write_text(json.dumps(data))
    (h.run / "config.lock").write_text(fingerprint(data) + "\n")


def test_the_registry_holds_the_formats_the_setting_accepts() -> None:
    assert set(FORMATS) == set(TRANSCRIPT_FORMATS)
    assert default_config().transcript_format == TRANSCRIPT_FORMATS[0]


def test_a_removed_field_name_is_refused_by_name(tmp_path: Path) -> None:
    c = tmp_path / "c.toml"
    c.write_text('[transcript]\nrole_key = "type"\nhandback_tool = ""\n')
    with pytest.raises(ConfigError) as caught:
        load_config(c)
    message = str(caught.value)
    assert "transcript.role_key, transcript.handback_tool: no longer a setting" in message
    assert "transcript.format" in message


class TestFrozenBeforeTheAdapters:
    def test_its_field_names_read_as_the_default_format(self, h: Harness) -> None:
        frozen_before_the_adapters(h)
        cfg = run_config(h.run)
        assert cfg.transcript_format == TRANSCRIPT_FORMATS[0]
        assert config_differences(cfg, default_config()) == []

    def test_a_judge_is_still_recorded_from_its_transcript(self, h: Harness) -> None:
        frozen_before_the_adapters(h)
        h.brief(1, "cand")
        meta = h.keys(1)["judges"][0]
        raw = judge_json(meta, h.texts(1, meta), {"candidate": FLAT, "control": BAD})
        brief = (round_dir(h.run, 1) / "brief_j1.md").read_text()
        rows = [
            {"type": "user", "agentId": "old1", "message": {"content": brief}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": raw}]}},
        ]
        t = h.tmp / "old1.jsonl"
        t.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        assert record_from(h.run, 1, 1, "old1", False, t)["ok"]

    def test_other_field_names_are_refused_by_name(self, h: Harness) -> None:
        frozen_before_the_adapters(h, handback_tool="", role_key="kind")
        with pytest.raises(EvalError) as caught:
            run_config(h.run)
        message = str(caught.value)
        assert "(transcript.role_key, transcript.handback_tool)" in message
        assert "finish it with the release that created it (0.4.0)" in message

    def test_the_lock_is_checked_before_the_field_names(self, h: Harness) -> None:
        frozen_before_the_adapters(h)
        data = json.loads((h.run / "config.json").read_text())
        data["transcript"]["handback_tool"] = "SomethingElse"  # edited, not relocked
        (h.run / "config.json").write_text(json.dumps(data))
        with pytest.raises(EvalError, match="changed after the freeze"):
            run_config(h.run)

    def test_a_partial_section_is_damaged(self, h: Harness) -> None:
        frozen_before_the_adapters(h)
        data = json.loads((h.run / "config.json").read_text())
        del data["transcript"]["role_key"]
        (h.run / "config.json").write_text(json.dumps(data))
        (h.run / "config.lock").write_text(fingerprint(data) + "\n")
        with pytest.raises(EvalError, match="damaged"):
            run_config(h.run)
