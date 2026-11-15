"""Malformed input gives a clear refusal, never a traceback; the spec's shape is checked."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, make_spec
from sealed_panel.cli import main
from sealed_panel.errors import EvalError
from sealed_panel.record import check_output
from sealed_panel.report import render_report, status
from sealed_panel.spec import init_run, load_frozen, validate_spec
from sealed_panel.store import round_dir


def errs(**over: Any) -> str:
    return "\n".join(validate_spec(make_spec(**over)))


class TestSpecShape:
    def test_a_spec_must_be_an_object(self) -> None:
        assert validate_spec([1, 2]) == ["the spec must be a JSON object"]

    def test_wrong_types_are_named(self) -> None:
        assert "spec.personas must be a list of objects" in errs(personas="coo")
        assert "spec.dimensions must be a list of objects" in errs(dimensions=["clarity"])
        assert "spec.title must be a string" in errs(title=3)
        assert "spec.framings must be a list of non-empty strings" in errs(framings=[""])
        bad_persona = [{"id": 1, "description": "d"}]
        assert "persona 1: id must be a string" in errs(personas=bad_persona)

    def test_anchors_are_checked(self) -> None:
        dim = make_spec()["dimensions"][0]
        assert "anchors must be an object" in errs(dimensions=[{**dim, "anchors": "a"}])
        anchors = {**dim["anchors"], "6": "beyond"}
        assert "unknown key '6'" in errs(dimensions=[{**dim, "anchors": anchors}])
        anchors = {**dim["anchors"], "3": 3}
        assert "every anchor must be a string" in errs(dimensions=[{**dim, "anchors": anchors}])
        assert "personas must be a list" in errs(dimensions=[{**dim, "personas": "coo"}])

    def test_unknown_keys_are_refused(self) -> None:
        assert "spec: unknown key 'titel'" in errs(titel="x")
        dim = make_spec()["dimensions"][0]
        assert "unknown key 'wieght'" in errs(dimensions=[{**dim, "wieght": 2}])
        assert "unknown key 'role'" in errs(
            personas=[{"id": "coo", "description": "d", "role": "x"}]
        )
        assert "unknown key 'limit'" in errs(measured=[{"id": "m", "description": "d", "limit": 3}])
        choice = [{"choice": "c", "user_said": "u", "why": "w"}]
        assert "unknown key 'why'" in errs(declared_choices=choice)


class TestFactsFile:
    def test_facts_file_is_read_and_frozen(self, h: Harness) -> None:
        f = h.tmp / "facts.md"
        f.write_text("The cache went live on 4 November.")
        h.init(facts_file=str(f))
        spec = load_frozen(h.run)
        assert spec["facts"] == "The cache went live on 4 November."
        assert "facts_file" not in spec

    def test_missing_facts_file_refused(self, h: Harness) -> None:
        with pytest.raises(EvalError, match="facts_file does not exist"):
            h.init(facts_file=str(h.tmp / "nope.md"))

    def test_facts_and_facts_file_together_refused(self, h: Harness) -> None:
        f = h.tmp / "facts.md"
        f.write_text("x")
        with pytest.raises(EvalError, match="not both"):
            h.init(facts="some text", facts_file=str(f))

    def test_path_like_facts_text_refused(self, h: Harness) -> None:
        for value in ("sources.pdf", "~/Documents/cv/facts", "/home/x/Shared Docs/facts (v2).md"):
            with pytest.raises(EvalError, match="use facts_file"):
                h.init(facts=value)
        h.init(facts="Revenue: 12.5M")


class TestCleanExit:
    def test_malformed_spec_json(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        sp = h.tmp / "spec.json"
        sp.write_text("{not json")
        assert main(["init", str(h.run), "--spec", str(sp)]) == 2
        assert "not valid JSON" in capsys.readouterr().err

    def test_spec_of_the_wrong_shape(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps({"title": "t", "personas": "coo"}))
        assert main(["init", str(h.run), "--spec", str(sp)]) == 2
        assert "spec.personas must be a list of objects" in capsys.readouterr().err

    def test_damaged_run_file(self, h: Harness, capsys: pytest.CaptureFixture[str]) -> None:
        h.init(measured=[{"id": "tone", "description": "house style passes"}])
        h.brief(1, "cand")
        (round_dir(h.run, 1) / "measured.json").write_text('{"tone": 1}')
        rc = main(["measure", str(h.run), "--round", "1", "--id", "tone", "--value", "x", "--pass"])
        assert rc == 2
        assert "unusable input" in capsys.readouterr().err

    def test_odd_reply_shapes_are_rejected_not_raised(self) -> None:
        texts = {"candidate": "Fraud fell after the scored queue went live in Lyon."}
        for out in ([], {"items": []}, {"items": {"A": []}}, {"items": {"A": {"dimensions": 3}}}):
            assert not check_output(out, {"A": "candidate"}, texts, ["a"])["ok"]


class TestReading:
    def test_one_quote_for_every_dimension_is_flagged(self, h: Harness) -> None:
        h.init()
        e = h.full(1, "cand", FLAT)  # the test judges reuse one quote per item
        assert e["one_quote_judges"] == [1, 2, 3]
        assert "quoted the same words for every dimension" in render_report(h.run)

    def test_distinct_quotes_are_not_flagged(self) -> None:
        texts = {"candidate": "Fraud fell after the scored queue went live in Lyon."}
        cell = {"reasoning": "r", "severity": "none", "score": 4}
        out = {
            "items": {
                "A": {
                    "dimensions": {
                        "a": {**cell, "quote": "Fraud fell after the scored"},
                        "b": {**cell, "quote": "queue went live in Lyon"},
                    }
                }
            }
        }
        r = check_output(out, {"A": "candidate"}, texts, ["a", "b"])
        assert r["items"]["candidate"]["one_quote"] is False

    def test_sealed_headline_shows_baseline_to_sealed(self, h: Harness) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", {"clarity": 3, "evidence": 3})
        h.full(1, "v2", {"clarity": 5, "evidence": 4}, sealed=True)  # baseline scored 4/4
        rep = render_report(h.run)
        assert "baseline -> sealed: clarity 4 -> 5; evidence 4 -> 4." in rep
        assert "composite (info)" in rep

    def test_status_is_read_only(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", {"clarity": 3, "evidence": 3})
        fb = h.run / "feedback" / "after_r1.md"
        assert fb.exists(), "aggregate writes the findings"
        fb.unlink()
        before = sorted(p.relative_to(h.run) for p in h.run.rglob("*"))
        d = status(h.run)
        assert d["decision"] == "CONTINUE" and "feedback" not in d
        assert sorted(p.relative_to(h.run) for p in h.run.rglob("*")) == before


class TestCliRounds:
    def test_round_defaults_to_one_only_when_sealed(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        args = ["--artefact", str(h.files["cand"]), "--control", str(h.files["ctrl"])]
        assert main(["brief", str(h.run), *args]) == 2
        assert "the round number is required" in capsys.readouterr().err
        assert main(["brief", str(h.run), "--sealed", *args]) == 0
        assert (h.run / "sealed" / "r1" / "brief_j1.md").exists()

    def test_config_is_accepted_only_by_init(self, h: Harness) -> None:
        h.init()
        with pytest.raises(SystemExit):
            main(["status", str(h.run), "--config", "c.toml"])

    def test_aggregate_names_the_findings_file(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.play(1, "cand", {"candidate": FLAT, "control": BAD})
        assert main(["aggregate", str(h.run), "--round", "1"]) == 0
        assert "feedback/after_r1.md" in capsys.readouterr().out
        assert Path(h.run / "feedback" / "after_r1.md").exists()


class TestSpecValues:
    @pytest.mark.parametrize(
        ("over", "needle"),
        [
            ({"measured": [{"id": 5, "description": "d", "max": 3}]}, "id must be a string"),
            ({"measured": [{"id": "m", "description": ["d"]}]}, "description must be a string"),
            (
                {"measured": [{"id": "m", "description": "d", "max": 1, "min": 5}]},
                "min must not exceed max",
            ),
            (
                {"measured": [{"id": "m", "description": "d"}, {"id": "m", "description": "e"}]},
                "unique id",
            ),
            (
                {"declared_choices": [{"choice": ["a"], "user_said": "b"}]},
                "choice must be a string",
            ),
            ({"judges_per_persona": 10}, "between 2 and 9"),
            ({"judges_per_persona": 10**6}, "between 2 and 9"),
            ({"framings": []}, "spec.framings must be a list of non-empty strings"),
        ],
    )
    def test_values_are_checked(self, over: dict[str, Any], needle: str) -> None:
        assert needle in errs(**over)

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_numbers_are_refused(self, bad: str, h: Harness) -> None:
        dim = make_spec()["dimensions"][0]
        text = json.dumps(make_spec(dimensions=[{**dim, "weight": 1}]))
        sp = h.tmp / "spec.json"
        sp.write_text(text.replace('"weight": 1', f'"weight": {bad}'))
        with pytest.raises(EvalError, match="finite number"):
            init_run(h.run, sp)
        m = make_spec(measured=[{"id": "m", "description": "d", "max": 1}])
        sp.write_text(json.dumps(m).replace('"max": 1', f'"max": {bad}'))
        with pytest.raises(EvalError, match="finite number"):
            init_run(h.run, sp)


class TestFactsPaths:
    def test_facts_file_is_relative_to_the_spec(self, h: Harness) -> None:
        folder = h.tmp / "specs"
        folder.mkdir()
        (folder / "facts.md").write_text("Facts beside the spec.")
        sp = folder / "spec.json"
        sp.write_text(json.dumps(make_spec(facts_file="facts.md")))
        init_run(h.run, sp)
        assert load_frozen(h.run)["facts"] == "Facts beside the spec."

    @pytest.mark.parametrize(
        "value",
        [
            "~/Documents/notes/cv/facts",
            "~/My Documents/facts",
            "./notes/facts",
            "../shared/facts here",
            "docs/facts",
            "/srv/team notes/facts",
        ],
    )
    def test_path_like_facts_refused(self, h: Harness, value: str) -> None:
        with pytest.raises(EvalError, match="use facts_file"):
            h.init(facts=value)

    def test_a_tilde_number_is_text(self, h: Harness) -> None:
        h.init(facts="~2/3 of revenue comes from renewals.")
        assert "renewals" in load_frozen(h.run)["facts"]


class TestPathEdges:
    @pytest.mark.parametrize("value", ["~/My Notes", "./drafts", "../facts for the pitch"])
    def test_a_home_or_relative_prefix_is_a_path(self, h: Harness, value: str) -> None:
        with pytest.raises(EvalError, match="use facts_file"):
            h.init(facts=value)

    def test_a_leading_slash_without_another_is_text(self, h: Harness) -> None:
        h.init(facts="/ marks a line break in the quoted lyrics; revenue grew 12 percent.")
        assert "revenue" in load_frozen(h.run)["facts"]


class TestExtensionRule:
    def test_an_extension_is_matched_in_any_case(self, h: Harness) -> None:
        with pytest.raises(EvalError, match="use facts_file"):
            h.init(facts="FACTS.MD")

    def test_only_a_one_line_value_is_a_path_by_extension(self, h: Harness) -> None:
        h.init(facts="Revenue grew 12 percent.\nThe full table is in notes.md")
        assert "notes.md" in load_frozen(h.run)["facts"]
