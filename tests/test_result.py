"""The result file (grade.json): written by the sealed aggregate step from the view the report
prints, its keys and types frozen, and read back only in a format this release knows."""

from __future__ import annotations

import json
import re
import stat
import types
import typing
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json
from sealed_panel.errors import EvalError
from sealed_panel.model import RESULT_FILE, RESULT_SCHEMA_VERSION, ResultFile
from sealed_panel.record import record_reply
from sealed_panel.report import HASH_CHARS, render_report
from sealed_panel.result import GradeResult, release, result_view, write_result
from sealed_panel.steps import aggregate_round
from sealed_panel.store import round_dir

FIVE = {"clarity": 5, "evidence": 5}

# The result file's keys and types, format 1. A change here is a change of the file's format:
# raise RESULT_SCHEMA_VERSION, teach GradeResult.read the older one, and say so in the CHANGELOG.
SCHEMA = {
    "ResultFile": {
        "schema_version": "int",
        "sealed_panel": "str",
        "run_schema_version": "int",
        "title": "str",
        "rubric_sha256": "str",
        "config_sha256": "str",
        "template": "TemplateRef | None",
        "candidate_hash": "str | None",
        "decision": "str",
        "stands": "bool",
        "why": "str",
        "verdict": "str",
        "grade": "dict[str, dict[str, ResultCell]]",
        "obtained": "ResultObtained",
        "findings": "dict[str, list[Finding]]",
        "judge": "ResultJudge",
        "flags": "ResultFlags",
        "attempts": "ResultAttempts",
        "settings": "list[str]",
    },
    "ResultCell": {
        "median": "float",
        "scores": "list[int]",
        "severities": "list[str]",
        "sd": "float",
        "baseline_median": "float | None",
    },
    "ResultObtained": {
        "loop_rounds": "int",
        "loop_decision": "str | None",
        "seats": "int",
        "composite": "float",
        "noise": "float",
        "measured": "list[Measurement]",
    },
    "Measurement": {"id": "str", "value": "str", "pass": "bool"},
    "TemplateRef": {"id": "str", "version": "int", "sha256": "str", "modified": "bool"},
    "Finding": {"judge": "int", "dimension": "str", "severity": "str", "text": "object"},
    "ResultJudge": {
        "mode": "str",
        "argv": "list[str]",
        "model_id": "str",
        "transcript_format": "str | None",
    },
    "ResultFlags": {
        "injection": "dict[str, list[str]]",
        "injection_acknowledged": "str | None",
        "pasted_replies": "bool",
        "judge_env_changed": "list[str]",
        "panel_env_changed": "list[str]",
        "prompt_extras": "dict[str, int]",
        "one_quote_judges": "list[int]",
    },
    "ResultAttempts": {"valid": "int", "rejected": "int", "void": "int", "sources": "list[str]"},
}


def _name(tp: Any) -> str:
    return re.sub(r"\b[a-z_]+\.", "", str(tp)) if not isinstance(tp, type) else tp.__name__


def describe(td: Any, found: dict[str, dict[str, str]]) -> None:
    """Every key of a TypedDict and of the TypedDicts it names, with its type as written."""
    hints = typing.get_type_hints(td)
    found[td.__name__] = {k: _name(v) for k, v in hints.items()}
    for v in hints.values():
        for inner in _inner(v):
            if typing.is_typeddict(inner) and inner.__name__ not in found:
                describe(inner, found)


def _inner(tp: Any) -> list[Any]:
    args = typing.get_args(tp)
    return [tp] if not args else [x for a in args for x in _inner(a)]


def conforms(value: Any, tp: Any) -> bool:
    """Whether a value read from JSON has the type the TypedDict declares."""
    if tp is object:
        return True
    if typing.is_typeddict(tp):
        hints = typing.get_type_hints(tp)
        keys = set(value) if isinstance(value, dict) else None
        required = set(tp.__required_keys__)
        return (
            keys is not None
            and required <= keys <= set(hints)
            and all(conforms(value[k], hints[k]) for k in keys)
        )
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):
        return any(conforms(value, a) for a in args)
    if origin is list:
        return isinstance(value, list) and all(conforms(v, args[0]) for v in value)
    if origin is dict:
        return isinstance(value, dict) and all(
            isinstance(k, str) and conforms(v, args[1]) for k, v in value.items()
        )
    if tp is type(None):
        return value is None
    if tp is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tp is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, tp)


def transcript(h: Harness, agent: str, prompt: str, reply: str) -> Path:
    folder = h.tmp / "tasks"
    folder.mkdir(exist_ok=True)
    answer = {"content": [{"type": "text", "text": reply}]}
    rows = [
        {"type": "user", "agentId": agent, "message": {"content": prompt}},
        {"type": "assistant", "agentId": agent, "message": answer},
    ]
    path = folder / f"{agent}.output"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def seal_with_notes(h: Harness) -> None:
    """A one-shot sealed panel in host mode with something for every note: judge 1 read from a
    transcript whose prompt carries text beyond its brief, judge 2 first rejected then pasted,
    judge 3 pasted."""
    h.init()
    h.brief(1, "cand", sealed=True)
    rdir = round_dir(h.run, 1, True)
    for meta in h.keys(1, sealed=True)["judges"]:
        sc = {r: (FLAT if r == "candidate" else BAD) for r in meta["key"].values()}
        raw = judge_json(meta, h.texts(1, meta, sealed=True), sc)
        j = meta["judge"]
        if j == 1:
            prompt = "Read carefully.\n" + (rdir / "brief_j1.md").read_text()
            t = transcript(h, "t1", prompt, raw)
            record_reply(h.run, 1, 1, "t1", raw, True, "transcript", t)
            continue
        if j == 2:
            record_reply(h.run, 1, 2, "bad2", "not json at all", True)
        record_reply(h.run, 1, j, f"p{j}", raw, True)
    aggregate_round(h.run, 1, True)


class TestTheFile:
    def test_nothing_before_the_sealed_panel(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT)
        assert result_view(h.run) is None
        assert not (h.run / RESULT_FILE).exists()

    def test_never_written_without_a_sealed_panel(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT)
        with pytest.raises(EvalError, match="no sealed panel is aggregated"):
            write_result(h.run)
        assert not (h.run / RESULT_FILE).exists()

    def test_the_release_is_unknown_when_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing(name: str) -> str:
            raise metadata.PackageNotFoundError(name)

        monkeypatch.setattr(metadata, "version", missing)
        assert release() == "unknown"

    def test_written_by_the_sealed_aggregate_owner_only(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        path = h.run / RESULT_FILE
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        data = json.loads(path.read_text())
        assert data == result_view(h.run)
        assert data["schema_version"] == RESULT_SCHEMA_VERSION
        assert data["decision"] == "SEALED" and data["stands"] is True and data["why"] == ""
        assert data["judge"] == {
            "mode": "host",
            "argv": [],
            "model_id": "",
            "transcript_format": "agent-jsonl",
        }
        assert data["obtained"]["loop_rounds"] == 0 and data["obtained"]["loop_decision"] is None
        assert data["grade"]["coo"]["clarity"]["scores"] == [5, 5, 5]

    def test_byte_stable_across_two_reads(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        path = h.run / RESULT_FILE
        first = path.read_bytes()
        assert GradeResult.read(path) == GradeResult.read(path)
        render_report(h.run)
        assert path.read_bytes() == first

    def test_the_loop_before_the_seal_is_recorded(self, h: Harness) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT)
        h.full(1, "cand", FIVE, sealed=True)
        data = json.loads((h.run / RESULT_FILE).read_text())
        assert data["obtained"]["loop_rounds"] == 1
        assert data["obtained"]["loop_decision"] == "CAP"
        assert data["grade"]["coo"]["evidence"]["baseline_median"] is None  # same text

    def test_the_keys_and_types_are_the_snapshot(self) -> None:
        found: dict[str, dict[str, str]] = {}
        describe(ResultFile, found)
        assert found == SCHEMA

    def test_a_written_file_has_those_types(self, h: Harness) -> None:
        seal_with_notes(h)
        data = json.loads((h.run / RESULT_FILE).read_text())
        assert conforms(data, ResultFile)
        assert set(data) == set(ResultFile.__required_keys__)


class TestSameViewAsTheReport:
    def test_every_field_the_report_prints_equals_the_file(self, h: Harness) -> None:
        seal_with_notes(h)
        data = json.loads((h.run / RESULT_FILE).read_text())
        rep = render_report(h.run)
        result = GradeResult.read(h.run / RESULT_FILE)
        assert {k: getattr(result, k) for k in data} == data
        assert result.run == h.run and result.path == h.run / RESULT_FILE
        assert f"Rubric sha256 `{data['rubric_sha256'][:HASH_CHARS]}`" in rep
        assert f"decision **{data['decision']}**" in rep
        assert (
            data["settings"]
            and "settings that differ from the defaults: " + "; ".join(data["settings"]) in rep
        )
        cells = "; ".join(
            f"{did} {c['median']:g} ({'/'.join(map(str, c['scores']))})"
            for p in data["grade"].values()
            for did, c in p.items()
        )
        assert f"**Grade (sealed panel): {cells}. Verdict: {data['verdict']}.**" in rep
        assert "Judge model: not declared." in rep and data["judge"]["model_id"] == ""
        notes = [x for x in rep.splitlines() if x.startswith("- sealed round 1:")]
        assert sum("rejected:" in x for x in notes) == data["attempts"]["rejected"] == 1
        assert sum("voided" in x for x in notes) == data["attempts"]["void"] == 0
        assert data["flags"]["pasted_replies"] and any("pasted" in x for x in notes)
        extras = {j: n for j, n in data["flags"]["prompt_extras"].items() if n}
        assert extras == {"1": len("Read carefully.\n")}
        assert [x for x in notes if "beyond its brief" in x] == [
            f"- sealed round 1: judge {j}'s prompt carried {n} characters beyond its brief; "
            "read what was added"
            for j, n in extras.items()
        ]
        assert data["attempts"]["valid"] == 3 and data["attempts"]["sources"] == [
            "output",
            "transcript",
        ]

    def test_a_blocked_panel_does_not_stand_in_the_file_or_the_report(self, h: Harness) -> None:
        """A sealed brief carrying text addressed to a judge is refused before it is written,
        so a blocked sealed panel needs a keys file changed by hand: the projection still says
        it does not stand, like the report."""
        h.init()
        h.brief(1, "cand", sealed=True)
        keys = round_dir(h.run, 1, True) / "keys.json"
        data = json.loads(keys.read_text())
        data["injection_flags_by_role"] = {"candidate": ["ignore previous instructions"]}
        keys.write_text(json.dumps(data))
        h.answer(1, {"candidate": FIVE, "control": BAD}, sealed=True)
        aggregate_round(h.run, 1, True)
        result = GradeResult.read(h.run / RESULT_FILE)
        rep = render_report(h.run)
        assert rep.startswith("# Evaluation: test pitch (BLOCKED BY FLAG)")
        assert "the grade below does not stand" in rep
        assert result.decision == "BLOCKED BY FLAG" and result.stands is False
        assert result.why and result.flags is not None
        assert result.flags["injection"] == {"candidate": ["ignore previous instructions"]}


class TestTheReader:
    def write(self, tmp_path: Path, **change: Any) -> Path:
        h = Harness(tmp_path)
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        path = h.run / RESULT_FILE
        data = {**json.loads(path.read_text()), **change}
        path.write_text(json.dumps(data))
        return path

    def test_a_later_format_is_refused_with_the_advice_to_upgrade(self, tmp_path: Path) -> None:
        path = self.write(tmp_path, schema_version=RESULT_SCHEMA_VERSION + 1, later_key=1)
        with pytest.raises(EvalError, match=r"newer release.*upgrade sealed-panel"):
            GradeResult.read(path)

    @pytest.mark.parametrize("change", ["added", "missing"])
    def test_a_file_without_its_format_keys_is_refused_as_damaged(
        self, tmp_path: Path, change: str
    ) -> None:
        path = self.write(tmp_path, later_key=1)
        if change == "missing":
            data = json.loads(path.read_text())
            del data["later_key"], data["verdict"]
            path.write_text(json.dumps(data))
        with pytest.raises(EvalError, match="is damaged"):
            GradeResult.read(path)

    @pytest.mark.parametrize("version", [0, "1", None, True])
    def test_a_file_without_a_format_is_not_a_result(self, tmp_path: Path, version: Any) -> None:
        path = self.write(tmp_path, schema_version=version)
        with pytest.raises(EvalError, match="not a sealed-panel result file"):
            GradeResult.read(path)
