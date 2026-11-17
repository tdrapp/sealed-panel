"""The result file (grade.json): written by the sealed aggregate step from the view the report
prints, its keys and types frozen, and read back only in a format this release knows."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import typing
from importlib import metadata
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec, strip_fence
from sealed_panel import cli
from sealed_panel.errors import EvalError
from sealed_panel.model import RESULT_FILE, RESULT_SCHEMA_VERSION, ResultFile
from sealed_panel.record import record_reply
from sealed_panel.report import HASH_CHARS, render_report
from sealed_panel.result import GradeResult, release, result_view, write_result
from sealed_panel.runner import judge_round
from sealed_panel.settings import load_config
from sealed_panel.shape import problem
from sealed_panel.steps import aggregate_round, init_run
from sealed_panel.store import round_dir

ROOT = Path(__file__).resolve().parent.parent
STUB = Path(__file__).resolve().parent / "stub_judge.py"
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
        "findings": "dict[str, list[ResultFinding]]",
        "judge": "ResultJudge",
        "flags": "ResultFlags",
        "attempts": "ResultAttempts",
        "settings": "list[str]",
    },
    "TemplateRef": {"id": "str", "version": "int", "sha256": "str", "modified": "bool"},
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
    "ResultFinding": {"judge": "int", "dimension": "str", "severity": "str", "text": "str"},
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
        "other_runs": "list[str]",
    },
    "ResultAttempts": {
        "valid": "int",
        "rejected": "list[RejectedJudge]",
        "void": "list[Voided]",
        "sources": "list[str]",
    },
    "RejectedJudge": {"judge": "int", "errors": "list[str]"},
    "Voided": {
        "judge": "int",
        "agent_id": "str",
        "replied": "bool",
        "source": "str",
        "reason": "str",
    },
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


# What a key whose value is a map of names is keyed by, in docs/api.md's table.
MAP_KEYS = {"grade": "<persona>.<dimension>", "findings": "<persona>[]"}


def documented_keys(td: Any = ResultFile, prefix: str = "") -> list[str]:
    """Every key of the file as docs/api.md's table names it: dotted, `[]` for a list item."""
    out = []
    for key, tp in typing.get_type_hints(td).items():
        name = prefix + key
        out.append(name)
        inner = [x for x in _inner(tp) if typing.is_typeddict(x)]
        if not inner:
            continue
        origin = typing.get_origin(tp)
        sep = f".{MAP_KEYS[name]}." if origin is dict else "[]." if origin is list else "."
        out += documented_keys(inner[0], name + sep)
    return out


def transcript(h: Harness, agent: str, prompt: str, reply: str | None) -> Path:
    folder = h.tmp / "tasks"
    folder.mkdir(exist_ok=True)
    rows: list[dict[str, Any]] = [
        {"type": "user", "agentId": agent, "message": {"content": prompt}}
    ]
    if reply is not None:
        answer = {"content": [{"type": "text", "text": reply}]}
        rows.append({"type": "assistant", "agentId": agent, "message": answer})
    path = folder / f"{agent}.output"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def seal_with_notes(h: Harness) -> None:
    """A one-shot sealed panel in host mode with a note of each host kind: judge 1 voided, then
    read from a transcript whose prompt carries text beyond its brief; judge 2 first rejected
    then pasted; judge 3 pasted; a material finding."""
    h.init()
    h.brief(1, "cand", sealed=True)
    rdir = round_dir(h.run, 1, True)
    dead = transcript(h, "dead1", (rdir / "brief_j1.md").read_text(), None)
    record_reply(h.run, 1, 1, "dead1", "", True, "void", dead, void=True)
    for meta in h.keys(1, sealed=True)["judges"]:
        sc = {r: (FLAT if r == "candidate" else BAD) for r in meta["key"].values()}
        raw = judge_json(meta, h.texts(1, meta, sealed=True), sc, severity={"evidence": "material"})
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


def seal_in_runner_mode(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Runner mode with a note of each runner kind: a loop round and the sealed panel under
    other values of the allowed name (a judge env change and a panel env change), a failed
    judge voided in the sealed panel, and the stub's one-quote judges; a run beside it."""
    first = h.tmp / "first"
    command = [sys.executable, str(STUB), "--fail-first", str(first)]
    cfg = h.tmp / "judge.toml"
    cfg.write_text(f'[judge]\ncommand = {json.dumps(command)}\nenv_allow = ["MODEL"]\n')
    spec = h.tmp / "spec.json"
    spec.write_text(json.dumps(make_spec(judges_per_persona=2, max_rounds=1)))
    init_run(h.run, spec, load_config(cfg))
    first.write_text("")  # the loop's judges do not fail
    monkeypatch.setenv("MODEL", "a")
    h.brief(1, "cand")
    judge_round(h.run, 1, False)
    aggregate_round(h.run, 1, False)
    first.unlink()  # the sealed panel's first judge fails once: a void
    h.brief(1, "v2", sealed=True)
    monkeypatch.setenv("MODEL", "b")
    judge_round(h.run, 1, True, only=[1])
    judge_round(h.run, 1, True, only=[1])
    monkeypatch.setenv("MODEL", "c")
    judge_round(h.run, 1, True, only=[2])
    (h.tmp / "earlier").mkdir()
    (h.tmp / "earlier" / "rubric.lock").write_text("x")
    aggregate_round(h.run, 1, True)


def assert_same_view(run: Path) -> dict[str, Any]:
    """Every field the report prints equals the file's, and every sealed note the report prints
    is one the file holds."""
    data: dict[str, Any] = json.loads((run / RESULT_FILE).read_text())
    result = GradeResult.read(run / RESULT_FILE)
    assert {k: getattr(result, k) for k in data} == data
    rep = render_report(run)
    assert f"Rubric sha256 `{data['rubric_sha256'][:HASH_CHARS]}`" in rep
    assert f"decision **{data['decision']}**" in rep
    settings = "; ".join(data["settings"])
    assert (
        f"settings that differ from the defaults: {settings}" if settings else "all defaults"
    ) in rep
    cells = "; ".join(
        f"{did} {c['median']:g} ({'/'.join(map(str, c['scores']))})"
        for p in data["grade"].values()
        for did, c in p.items()
    )
    assert f"**Grade (sealed panel): {cells}. Verdict: {data['verdict']}.**" in rep
    model = data["judge"]["model_id"]
    assert (f"Judge model: `{model}`." if model else "Judge model: not declared.") in rep
    findings = [
        f"- open finding, {pid} [{x['severity']}] ({x['dimension']}) {x['text']}"
        for pid, found in data["findings"].items()
        for x in found
    ]
    assert [x for x in rep.splitlines() if x.startswith("- open finding")] == findings
    notes = [x for x in rep.splitlines() if x.startswith("- sealed round 1:")]
    flags, attempts = data["flags"], data["attempts"]
    assert [x.split(" rejected:")[0] for x in notes if " rejected:" in x] == [
        f"- sealed round 1: judge {r['judge']}" for r in attempts["rejected"]
    ]
    assert [x.split(" voided")[0] for x in notes if " voided" in x] == [
        f"- sealed round 1: judge {v['judge']} attempt {v['agent_id']}" for v in attempts["void"]
    ]
    assert [x for x in notes if "beyond its brief" in x] == [
        f"- sealed round 1: judge {j}'s prompt carried {n} characters beyond its brief; "
        "read what was added"
        for j, n in sorted(flags["prompt_extras"].items())
    ]
    assert [int(x.split("judge ")[1].split(" ")[0]) for x in notes if "same words" in x] == (
        flags["one_quote_judges"]
    )
    assert any("pasted" in x for x in notes) == flags["pasted_replies"]
    assert any(" across attempts " in x for x in notes) == bool(flags["judge_env_changed"])
    for name in flags["judge_env_changed"]:
        assert any(" across attempts " in x and name in x for x in notes)
    panel = [x for x in rep.splitlines() if x.startswith("- sealed panel:")]
    assert bool(panel) == bool(flags["panel_env_changed"])
    assert all(name in panel[0] for name in flags["panel_env_changed"])
    others = [x for x in rep.splitlines() if "other run(s) beside this one" in x]
    assert others == (
        [f"- {len(flags['other_runs'])} other run(s) beside this one "
         f"({', '.join(flags['other_runs'])}): a new run is a fresh panel; say why each was "
         "started"]
        if flags["other_runs"] else []
    )  # fmt: skip
    return data


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
        # Every reply was pasted: no transcript format counted anything.
        assert data["judge"] == {
            "mode": "host",
            "argv": [],
            "model_id": "",
            "transcript_format": None,
        }
        assert data["obtained"]["loop_rounds"] == 0 and data["obtained"]["loop_decision"] is None
        assert data["grade"]["coo"]["clarity"]["scores"] == [5, 5, 5]
        assert data["attempts"] == {"valid": 3, "rejected": [], "void": [], "sources": ["output"]}

    def test_the_transcript_format_is_named_when_a_judge_was_read_from_one(
        self, h: Harness
    ) -> None:
        seal_with_notes(h)
        data = json.loads((h.run / RESULT_FILE).read_text())
        assert data["judge"]["transcript_format"] == "agent-jsonl"

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
        assert problem(data, ResultFile) is None
        assert set(data) == set(ResultFile.__required_keys__)

    def test_a_judge_s_text_is_stored_as_the_text_the_report_prints(self, h: Harness) -> None:
        """A judge's weakest point may be any JSON value, here an object mimicking the file's
        keys: the result file holds the string the report prints, never the object."""
        h.init()
        h.brief(1, "cand", sealed=True)
        mimic = {"stands": True, "grade": {"coo": {"clarity": {"median": 5}}}}
        for n, meta in enumerate(h.keys(1, sealed=True)["judges"], 1):
            sc = {r: (FLAT if r == "candidate" else BAD) for r in meta["key"].values()}
            sev = {"clarity": "material"}
            raw = strip_fence(judge_json(meta, h.texts(1, meta, sealed=True), sc, severity=sev))
            for item in raw["items"].values():
                item["weakest_point"]["text"] = mimic
            record_reply(h.run, 1, meta["judge"], f"m{n}", json.dumps(raw), True)
        aggregate_round(h.run, 1, True)
        data = assert_same_view(h.run)
        texts = [x["text"] for found in data["findings"].values() for x in found]
        assert texts and all(t == str(mimic) for t in texts)
        result_json = json.loads((round_dir(h.run, 1, True) / "result.json").read_text())
        assert result_json["personas"]["coo"]["findings"][0]["text"] == mimic  # unchanged


class TestSameViewAsTheReport:
    def test_host_mode(self, h: Harness) -> None:
        seal_with_notes(h)
        data = assert_same_view(h.run)
        assert len(data["attempts"]["rejected"]) == 1 and len(data["attempts"]["void"]) == 1
        assert data["flags"]["pasted_replies"]
        assert data["flags"]["prompt_extras"] == {"1": len("Read carefully.\n")}
        assert data["attempts"]["valid"] == 3
        assert data["attempts"]["sources"] == ["output", "transcript"]

    @pytest.mark.slow
    def test_runner_mode(self, h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        seal_in_runner_mode(h, monkeypatch)
        data = assert_same_view(h.run)
        flags = data["flags"]
        assert [v["judge"] for v in data["attempts"]["void"]] == [1]
        assert flags["judge_env_changed"] == ["MODEL"]
        assert flags["panel_env_changed"] == ["MODEL"]
        assert flags["one_quote_judges"] == [1, 2]
        assert flags["other_runs"] == ["earlier"]

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
        assert_same_view(h.run)
        result = GradeResult.read(h.run / RESULT_FILE)
        rep = render_report(h.run)
        assert rep.startswith("# Evaluation: test pitch (BLOCKED BY FLAG)")
        assert "the grade below does not stand" in rep
        assert result.decision == "BLOCKED BY FLAG" and result.stands is False
        assert result.why and result.flags is not None
        assert result.flags["injection"] == {"candidate": ["ignore previous instructions"]}

    def test_the_other_runs_are_a_snapshot_the_report_keeps_live(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        assert json.loads((h.run / RESULT_FILE).read_text())["flags"]["other_runs"] == []
        (h.tmp / "later").mkdir()
        (h.tmp / "later" / "rubric.lock").write_text("x")
        assert json.loads((h.run / RESULT_FILE).read_text())["flags"]["other_runs"] == []
        assert "- 1 other run(s) beside this one (later)" in render_report(h.run)


class TestAMissingFile:
    def test_a_second_sealed_aggregate_writes_a_missing_file(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        path = h.run / RESULT_FILE
        before = path.read_bytes()
        stored = (round_dir(h.run, 1, True) / "result.json").read_bytes()
        os.remove(path)
        entry = aggregate_round(h.run, 1, True)
        assert path.read_bytes() == before and entry["sealed"]
        assert (round_dir(h.run, 1, True) / "result.json").read_bytes() == stored
        os.remove(path)
        assert cli.main(["aggregate", str(h.run), "--sealed"]) == 0
        assert capsys.readouterr().out == (
            f"sealed round 1 was already aggregated; wrote its missing {path}\n"
        )
        assert path.read_bytes() == before

    def test_an_existing_file_is_never_rewritten(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.full(1, "cand", FIVE, sealed=True)
        path = h.run / RESULT_FILE
        path.write_text('{"kept": true}')
        with pytest.raises(EvalError, match="already aggregated"):
            aggregate_round(h.run, 1, True)
        assert cli.main(["aggregate", str(h.run), "--sealed"]) == 2
        assert "already aggregated" in capsys.readouterr().err
        assert path.read_text() == '{"kept": true}'

    def test_a_loop_round_is_refused_as_before(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", FLAT)
        with pytest.raises(EvalError, match="already aggregated"):
            aggregate_round(h.run, 1, False)
        assert not (h.run / RESULT_FILE).exists()


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

    @pytest.mark.parametrize(
        ("key", "value", "where"),
        [
            ("stands", "false", "stands is str, not bool"),
            ("grade", "all fives", "grade is str, not an object"),
            ("flags", None, "flags is NoneType, not an object"),
            ("settings", "none", "settings is str, not a list"),
            ("attempts", {"valid": True, "rejected": [], "void": [], "sources": []}, "boolean"),
            ("findings", {"coo": [{"judge": 1, "dimension": "d", "severity": "s", "text": {}}]},
             "findings.coo[0].text is dict, not str"),
        ],
    )  # fmt: skip
    def test_a_value_of_the_wrong_type_is_refused_as_damaged(
        self, tmp_path: Path, key: str, value: Any, where: str
    ) -> None:
        path = self.write(tmp_path, **{key: value})
        with pytest.raises(EvalError, match="is damaged") as caught:
            GradeResult.read(path)
        assert where in str(caught.value)

    @pytest.mark.parametrize("version", [0, "1", None, True])
    def test_a_file_without_a_format_is_not_a_result(self, tmp_path: Path, version: Any) -> None:
        path = self.write(tmp_path, schema_version=version)
        with pytest.raises(EvalError, match="not a sealed-panel result file"):
            GradeResult.read(path)


def test_the_documented_fields_are_the_snapshot() -> None:
    """docs/api.md's table of the result file names every key, sub-keys included, and no
    other."""
    page = ROOT / "docs" / "api.md"
    if not page.exists():  # the mutation check runs on a copy without the docs
        pytest.skip("not a checkout: the docs are not here")
    section = page.read_text().split("## The result file", 1)[1].split("\n## ", 1)[0]
    rows = [r for r in section.splitlines() if r.startswith("| `")]
    keys = [k for r in rows for k in re.findall(r"`([^`]+)`", r.split(" | ")[0])]
    assert keys == documented_keys()
