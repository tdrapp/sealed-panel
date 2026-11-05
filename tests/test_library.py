"""The rubric library: template format, suggestions, skeletons, candidates and promotion."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from conftest import FLAT, Harness, make_spec
from sealed_panel import config, library
from sealed_panel.brief import write_briefs
from sealed_panel.cli import main
from sealed_panel.config import default_library_dir, library_dir, load_config
from sealed_panel.errors import EvalError
from sealed_panel.report import render_report
from sealed_panel.spec import RUBRIC_KEYS, TODO_MARKER, init_run, load_frozen, rubric_hash
from sealed_panel.spec import validate_spec as validate

BUILTINS = ("cv", "cover-letter", "pitch", "message", "essay", "interview-answer", "plan-doc",
            "prompt-output")  # fmt: skip
RUN = "0" * 64


def filled(value: Any) -> Any:
    """A skeleton with every TODO marker replaced: text, or a number for a threshold."""
    if isinstance(value, str):
        if value.startswith(TODO_MARKER + ": your "):
            return 100
        return value.replace(TODO_MARKER + ": ", "").replace(TODO_MARKER, "filled")
    if isinstance(value, dict):
        return {k: filled(v) for k, v in value.items()}
    if isinstance(value, list):
        return [filled(v) for v in value]
    return value


def proposal(change: dict[str, Any], template: dict[str, Any] | None = None,
             dimension: str = "ask", kind: str = "judges_disagree",
             rubric: str = RUN) -> dict[str, Any]:  # fmt: skip
    if template is None:
        template = {"id": "message", "version": 1}
    return {
        "kind": kind,
        "dimension": dimension,
        "run": {"rubric_sha256": rubric, "template": template or None, "run_dir": "/runs/x"},
        "evidence": [
            {"run": rubric, "round": 1, "judges": [1, 2], "quotes": [], "note": dimension}
        ],
        "proposed_change": change,
    }


def candidate(lib: Path, change: dict[str, Any], **kw: Any) -> str:
    path = library.add_candidate(lib, proposal(change, **kw))
    assert path is not None
    return path.stem


def rewrite(did: str = "ask", **anchors: str) -> dict[str, Any]:
    return {"action": "rewrite_anchors", "dimension": did, "anchors": anchors or {"5": "new five"}}


class TestTemplates:
    def test_every_builtin_is_valid(self) -> None:
        found = library.builtin_templates()
        assert sorted(found) == sorted(BUILTINS)
        for t in found.values():
            assert library.template_errors(t) == [], t["id"]
            assert t["facts_hint"] and t["aliases"]

    @pytest.mark.parametrize("tid", BUILTINS)
    def test_every_builtin_instantiates_into_a_valid_spec(self, tid: str, tmp_path: Path) -> None:
        t = library.builtin_templates()[tid]
        skeleton = library.instantiate(t)
        assert any(TODO_MARKER in e for e in validate(skeleton))
        assert t["facts_hint"] in skeleton["facts"]
        for m in skeleton["measured"]:
            assert all(TODO_MARKER in str(m[b]) for b in ("max", "min") if b in m)
        spec = filled(skeleton)
        assert validate(spec) == []
        sp = tmp_path / "spec.json"
        sp.write_text(json.dumps(spec))
        init_run(tmp_path / "run", sp)
        ref = load_frozen(tmp_path / "run")["template"]
        assert (ref["id"], ref["version"], ref["modified"]) == (tid, 1, False)

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"bogus": 1}, "unknown key"),
            ({"id": "Bad Id"}, "id must be lowercase"),
            ({"version": 0}, "version must be an integer"),
            ({"kinds": []}, "kinds must be a non-empty list"),
            ({"aliases": [""]}, "aliases must be a list of strings"),
            ({"facts_hint": " "}, "facts_hint must be a non-empty string"),
            ({"based_on": {"version": "1"}}, "based_on must be an object"),
            ({"title": " "}, "title must be a non-empty string"),
            ({"personas": []}, "personas must be a non-empty list"),
            ({"personas": [{"id": "x"}]}, "exactly an id and a description"),
            ({"measured": {}}, "measured must be a list"),
            ({"provenance": []}, "provenance must be a non-empty list"),
            ({"provenance": [{"date": "d"}]}, "exactly date, source and change"),
        ],
    )
    def test_format_errors_are_named(self, change: dict[str, Any], message: str) -> None:
        t = {**library.builtin_templates()["message"], **change}
        assert any(message in e for e in library.template_errors(t))

    def test_dimension_rules(self) -> None:
        t = library.builtin_templates()["message"]
        dims = t["dimensions"]
        errs = library.template_errors
        assert any("dimensions must be" in e for e in errs({**t, "dimensions": dims[:2]}))
        many = [{**dims[0], "id": f"d{i}"} for i in range(7)]
        assert any("dimensions must be" in e for e in errs({**t, "dimensions": many}))
        assert "dimension ids must be unique" in errs(
            {**t, "dimensions": [dims[0], dims[0], dims[1]]}
        )
        no5 = [{**dims[0], "anchors": {"1": "a", "3": "b"}}, dims[1], dims[2]]
        assert any("anchors 1, 3 and 5" in e for e in errs({**t, "dimensions": no5}))
        odd = [{**dims[0], "extra": 1}, dims[1], dims[2]]
        assert any("exactly id, name" in e for e in errs({**t, "dimensions": odd}))
        todo = [{**dims[0], "question": TODO_MARKER + ": x"}, dims[1], dims[2]]
        assert any("markers" in e for e in errs({**t, "dimensions": todo}))
        assert errs([]) == ["a template must be a JSON object"]
        assert any("missing key" in e for e in errs({"id": "x"}))

    def test_user_template_overrides_and_newer_builtin_is_flagged(
        self, private_library: Path
    ) -> None:
        t = {**library.builtin_templates()["message"], "version": 3, "title": "Team messages",
             "based_on": {"version": 0}}  # fmt: skip
        private_library.mkdir(parents=True)
        (private_library / "team.json").write_text(json.dumps(t))
        served = library.get_template(private_library, "message")
        assert served["template"]["title"] == "Team messages"
        assert served["path"] == private_library / "team.json"
        assert "user (overrides built-in v1)" in served["source"]
        assert "built-in is newer than your base (v0)" in served["source"]

    def test_invalid_user_files_are_skipped_with_a_warning(self, private_library: Path) -> None:
        private_library.mkdir(parents=True)
        (private_library / "broken.json").write_text('{"id": "broken"}')
        (private_library / "garbage.json").write_text("{not json")
        served, warnings = library.all_templates(private_library)
        assert "message" in served and "broken" not in served
        assert len(warnings) == 2 and all(w.startswith("skipped") for w in warnings)

    def test_unknown_template_refused(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="no template"):
            library.get_template(private_library, "nope")


class TestLibraryPath:
    def test_precedence_and_expansion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert library_dir(tmp_path / "flag") == tmp_path / "flag"
        monkeypatch.setenv("SEALED_PANEL_RUBRICS", "~/env-lib")
        assert library_dir() == Path.home() / "env-lib"
        monkeypatch.delenv("SEALED_PANEL_RUBRICS")
        assert library_dir() == default_library_dir()
        assert default_library_dir().is_absolute()
        assert str(default_library_dir()).startswith(str(Path.home()))
        assert not str(default_library_dir()).startswith(os.getcwd() + os.sep + "rubrics")

    def test_init_freezes_the_library(self, h: Harness, private_library: Path) -> None:
        h.init()
        frozen = json.loads((h.run / "config.json").read_text())["library"]["path"]
        assert frozen == str(private_library)
        assert not any("library" in x for x in render_report(h.run).splitlines()[2:3])


class TestSuggest:
    @pytest.mark.parametrize("tid", BUILTINS)
    def test_each_kind_and_alias_finds_its_template(self, tid: str, private_library: Path) -> None:
        t = library.builtin_templates()[tid]
        for kind in [*t["kinds"], *t["aliases"]]:
            for variant in (kind, kind.upper(), kind.replace("-", " "), kind.replace("-", "_")):
                rows = library.suggest(private_library, variant)
                assert rows and rows[0]["id"] == tid, variant

    def test_no_match_is_empty(self, private_library: Path) -> None:
        assert library.suggest(private_library, "limerick") == []

    def test_keywords_break_ties_by_whole_word(self, private_library: Path) -> None:
        t = {**library.builtin_templates()["message"], "id": "message-b", "keywords": ["ship"]}
        private_library.mkdir(parents=True)
        (private_library / "b.json").write_text(json.dumps(t))
        assert [r["id"] for r in library.suggest(private_library, "email", "")] == [
            "message",
            "message-b",
        ]
        rows = library.suggest(private_library, "email", "We ship today.")
        assert rows[0]["id"] == "message-b" and rows[0]["keywords"] == ["ship"]
        rows = library.suggest(private_library, "email", "Our shipping notice.")
        assert rows[0]["id"] == "message", "a keyword inside another word does not count"

    def test_top_must_be_positive(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="--top"):
            library.suggest(private_library, "essay", top=0)


class TestUse:
    def test_skeleton_freezes_the_template(self, tmp_path: Path, private_library: Path) -> None:
        out = library.write_skeleton(private_library, "message", tmp_path / "spec.json")
        skeleton = json.loads(out.read_text())
        assert skeleton["template"]["sha256"] == library.dimensions_sha(skeleton["dimensions"])
        with pytest.raises(EvalError, match="TODO"):
            init_run(tmp_path / "run", out)
        out.write_text(json.dumps(filled(skeleton)))
        init_run(tmp_path / "run", out)
        spec = json.loads((tmp_path / "run" / "spec.json").read_text())
        spec["template"]["version"] = 2
        (tmp_path / "run" / "spec.json").write_text(json.dumps(spec))
        with pytest.raises(EvalError, match="rubric changed"):
            load_frozen(tmp_path / "run")

    def test_edited_dimensions_are_flagged_modified(
        self, h: Harness, private_library: Path
    ) -> None:
        sp = h.tmp / "spec.json"
        skeleton = filled(library.instantiate(library.builtin_templates()["message"]))
        skeleton["dimensions"][0]["anchors"]["5"] = "my own five"
        sp.write_text(json.dumps(skeleton))
        init_run(h.run, sp)
        assert load_frozen(h.run)["template"]["modified"] is True
        h.brief(1, "cand")
        assert "template message v1 (modified)" in render_report(h.run)

    def test_persona_file_and_refusals(self, tmp_path: Path, private_library: Path) -> None:
        pf = tmp_path / "personas.json"
        pf.write_text(json.dumps([{"id": "lead", "description": "Team lead who decides"}]))
        out = library.write_skeleton(private_library, "message", tmp_path / "a.json", pf)
        assert json.loads(out.read_text())["personas"][0]["id"] == "lead"
        with pytest.raises(EvalError, match="already exists"):
            library.write_skeleton(private_library, "message", out)
        pf.write_text('{"id": "lead"}')
        with pytest.raises(EvalError, match="list of personas"):
            library.write_skeleton(private_library, "message", tmp_path / "b.json", pf)
        pf.write_text('[{"id": "lead", "role": "x"}]')
        with pytest.raises(EvalError, match="would not be a valid spec"):
            library.write_skeleton(private_library, "message", tmp_path / "c.json", pf)

    def test_a_spec_without_template_keeps_its_old_hash(self) -> None:
        spec = make_spec()
        core = {k: spec.get(k) for k in RUBRIC_KEYS if k != "template"}
        old = hashlib.sha256(
            json.dumps(core, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        assert rubric_hash(spec) == old

    def test_a_template_reference_has_a_shape(self) -> None:
        assert "spec.template must be an object" in "\n".join(validate(make_spec(template="x")))
        bad = make_spec(template={"id": "message", "version": "1"})
        assert any("integer version" in e for e in validate(bad))
        extra = make_spec(template={"id": "message", "version": 1, "src": "x"})
        assert any("unknown key 'src'" in e for e in validate(extra))


class TestCandidates:
    def test_ids_are_checked_before_paths(self, private_library: Path) -> None:
        for bad in ("../../etc/passwd", "c-123", "C-0123456789ab", "c-0123456789ab/x"):
            with pytest.raises(EvalError, match="not a candidate id"):
                library.promote(private_library, bad)

    def test_the_id_inside_a_file_is_never_trusted(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        path = private_library / "inbox" / f"{cid}.json"
        data = json.loads(path.read_text())
        path.write_text(json.dumps({**data, "id": "../../evil"}))
        rows, _ = library.open_candidates(private_library)
        assert rows[0]["id"] == cid
        library.reject(private_library, cid, "no")
        assert (private_library / "history" / f"{cid}.json").exists()

    def test_unreadable_inbox_files_are_skipped(self, private_library: Path) -> None:
        candidate(private_library, rewrite())
        (private_library / "inbox" / "c-ffffffffffff.json").write_text("{broken")
        (private_library / "inbox" / "notes.json").write_text("{}")
        rows, warnings = library.open_candidates(private_library)
        assert len(rows) == 1 and len(warnings) == 2

    def test_dedup_by_template_version_and_case(self, private_library: Path) -> None:
        a = candidate(private_library, rewrite(), dimension="Ask")
        b = candidate(private_library, rewrite(), dimension="ask")
        c = candidate(private_library, rewrite(), template={"id": "message", "version": 2})
        d = candidate(private_library, rewrite(), template={"id": "pitch", "version": 1})
        assert a == b and len({a, c, d}) == 3

    def test_dedup_without_a_template_uses_the_rubric_hash(self, private_library: Path) -> None:
        a = candidate(private_library, rewrite(), template={}, rubric="1" * 64)
        b = candidate(private_library, rewrite(), template={}, rubric="1" * 64)
        c = candidate(private_library, rewrite(), template={}, rubric="2" * 64)
        assert a == b != c

    def test_evidence_is_not_added_twice(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        candidate(private_library, rewrite())
        rows, _ = library.open_candidates(private_library)
        assert (rows[0]["id"], rows[0]["count"]) == (cid, 1)

    def test_a_rejected_key_is_not_reopened(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        library.reject(private_library, cid, "the anchor is right")
        assert library.add_candidate(private_library, proposal(rewrite())) is None
        new = candidate(private_library, rewrite(), template={"id": "message", "version": 2})
        c = json.loads((private_library / "inbox" / f"{new}.json").read_text())
        assert c["rejected_before"][0]["reason"] == "the anchor is right"

    def test_a_promoted_key_is_not_reopened(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        library.promote(private_library, cid)
        assert library.add_candidate(private_library, proposal(rewrite())) is None
        assert library.open_candidates(private_library)[0] == []

    def test_a_modified_template_never_merges_with_the_original(
        self, private_library: Path
    ) -> None:
        plain = candidate(private_library, rewrite(), template={"id": "message", "version": 1})
        edited = candidate(
            private_library, rewrite(), template={"id": "message", "version": 1, "modified": True}
        )
        assert plain != edited

    def test_every_run_is_kept(self, private_library: Path) -> None:
        first = proposal(rewrite())
        second = proposal(rewrite())
        second["run"] = {**second["run"], "run_dir": "/runs/y"}
        second["evidence"] = [{**second["evidence"][0], "run_dir": "/runs/y"}]
        library.add_candidate(private_library, first)
        path = library.add_candidate(private_library, second)
        assert path is not None
        c = json.loads(path.read_text())
        assert [r["run_dir"] for r in c["runs"]] == ["/runs/x", "/runs/y"]
        assert c["count"] == 2

    def test_a_closed_candidate_left_in_the_inbox_is_refused(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        path = private_library / "inbox" / f"{cid}.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "status": "rejected"}))
        with pytest.raises(EvalError, match="is rejected, not open"):
            library.promote(private_library, cid)

    def test_unknown_candidate_kind_refused(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="unknown candidate kind"):
            library.add_candidate(private_library, proposal(rewrite(), kind="guess"))


class TestPromote:
    def test_promote_copies_a_builtin_and_bumps_the_version(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite(**{"5": "the ask is in the first line"}))
        path = library.promote(private_library, cid)
        t = json.loads(path.read_text())
        assert path == private_library / "message.json"
        assert (t["version"], t["based_on"]) == (2, {"version": 1})
        assert t["dimensions"][0]["anchors"]["5"] == "the ask is in the first line"
        assert t["provenance"][:-1] == library.builtin_templates()["message"]["provenance"]
        assert t["provenance"][-1]["source"].startswith(f"candidate {cid}")
        assert library.builtin_templates()["message"]["version"] == 1, "built-ins never change"
        closed = json.loads((private_library / "history" / f"{cid}.json").read_text())
        assert closed["promoted_into"] == {"id": "message", "version": 2}
        assert not (private_library / "inbox" / f"{cid}.json").exists()
        with pytest.raises(EvalError, match="no open candidate"):
            library.promote(private_library, cid)

    def test_the_version_goes_above_the_builtin(
        self, private_library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real = library.builtin_templates()
        newer = {**real, "message": {**real["message"], "version": 5}}
        monkeypatch.setattr(library, "builtin_templates", lambda: newer)
        user = {**real["message"], "version": 2}
        private_library.mkdir(parents=True)
        (private_library / "message.json").write_text(json.dumps(user))
        t = json.loads(
            library.promote(private_library, candidate(private_library, rewrite())).read_text()
        )
        assert t["version"] == 6

    def test_into_takes_precedence(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite("structure"))
        path = library.promote(private_library, cid, into="pitch")
        assert path.name == "pitch.json"
        assert not (private_library / "message.json").exists()

    def test_promote_writes_the_served_file(self, private_library: Path) -> None:
        private_library.mkdir(parents=True)
        (private_library / "team.json").write_text(
            json.dumps(library.builtin_templates()["message"])
        )
        path = library.promote(private_library, candidate(private_library, rewrite()))
        assert path == private_library / "team.json"
        assert not (private_library / "message.json").exists()

    def test_an_invalid_user_file_for_the_id_blocks_promotion(self, private_library: Path) -> None:
        private_library.mkdir(parents=True)
        (private_library / "message.json").write_text("{broken")
        with pytest.raises(EvalError, match="fix or remove it first"):
            library.promote(private_library, candidate(private_library, rewrite()))
        assert (private_library / "message.json").read_text() == "{broken"

    def test_promote_refuses_todo_markers_in_used_fields(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite(**{"5": TODO_MARKER + ": write it"}))
        with pytest.raises(EvalError, match="edit the candidate"):
            library.promote(private_library, cid)
        assert not (private_library / "message.json").exists()

    def test_remove_dimension_ignores_unused_todo_anchors(self, private_library: Path) -> None:
        change = {"action": "remove_dimension", "dimension": "brevity",
                  "anchors": {"5": TODO_MARKER + ": unused"}}  # fmt: skip
        t = json.loads(
            library.promote(private_library, candidate(private_library, change)).read_text()
        )
        assert [d["id"] for d in t["dimensions"]] == ["ask", "relevance", "evidence"]

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"action": "rename"}, "unknown action"),
            ({"action": "remove_dimension", "dimension": "ghost"}, "no dimension 'ghost'"),
            ({"action": "add_dimension", "dimension": {"id": "ask"}}, "id not in the template"),
            (
                {"action": "rewrite_anchors", "dimension": "ask", "anchors": {}},
                "needs the new anchors",
            ),
            ({"action": "add_dimension", "dimension": {"id": "tone"}}, "not a valid template"),
        ],
    )
    def test_inapplicable_changes_refused(
        self, private_library: Path, change: dict[str, Any], message: str
    ) -> None:
        with pytest.raises(EvalError, match=message):
            library.promote(private_library, candidate(private_library, change))

    def test_into_is_needed_without_a_template(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite(), template={})
        with pytest.raises(EvalError, match="--into"):
            library.promote(private_library, cid)
        library.promote(private_library, cid, into="message")

    def test_promotion_is_all_or_nothing(
        self, private_library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = candidate(private_library, rewrite())

        def fail(src: Any, dst: Any) -> None:
            raise OSError("no space left")

        monkeypatch.setattr(library.os, "replace", fail)
        with pytest.raises(OSError):
            library.promote(private_library, cid)
        assert not (private_library / "message.json").exists()
        assert (private_library / "inbox" / f"{cid}.json").exists()
        assert not list(private_library.rglob("*.staged"))

    def test_a_failed_second_move_rolls_back_the_first(
        self, private_library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        private_library.mkdir(parents=True)
        original = json.dumps(library.builtin_templates()["message"])
        (private_library / "message.json").write_text(original)
        cid = candidate(private_library, rewrite())
        real = os.replace
        calls = []

        def second_fails(src: Any, dst: Any) -> None:
            calls.append(dst)
            if len(calls) == 2:
                raise OSError("disk full")
            real(src, dst)

        monkeypatch.setattr(library.os, "replace", second_fails)
        with pytest.raises(OSError):
            library.promote(private_library, cid)
        assert (private_library / "message.json").read_text() == original
        assert (private_library / "inbox" / f"{cid}.json").exists()
        assert not list(private_library.rglob("*.staged"))

    def test_a_file_holding_another_template_is_never_overwritten(
        self, private_library: Path
    ) -> None:
        private_library.mkdir(parents=True)
        other = {**library.builtin_templates()["pitch"], "id": "pitch-team"}
        (private_library / "message.json").write_text(json.dumps(other))
        with pytest.raises(EvalError, match="holds a template of another id"):
            library.promote(private_library, candidate(private_library, rewrite()))
        assert json.loads((private_library / "message.json").read_text())["id"] == "pitch-team"

    def test_side_by_side_shows_both_texts(self, private_library: Path) -> None:
        plan = library.plan_promotion(
            private_library, candidate(private_library, rewrite(**{"5": "NEW"}))
        )
        lines = "\n".join(library.side_by_side(plan["current"], plan["updated"]))
        assert "dimension ask" in lines and "NEW" in lines
        assert library.builtin_templates()["message"]["dimensions"][0]["anchors"]["5"][:20] in lines

    def test_reject_moves_with_the_reason(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        with pytest.raises(EvalError, match="--reason"):
            library.reject(private_library, cid, " ")
        closed = json.loads(library.reject(private_library, cid, "keep it").read_text())
        assert (closed["status"], closed["reason"]) == ("rejected", "keep it")
        assert library.open_candidates(private_library)[0] == []


class TestFrozenRun:
    def test_a_promotion_does_not_change_a_frozen_run(
        self, h: Harness, private_library: Path
    ) -> None:
        sp = h.tmp / "spec.json"
        sp.write_text(
            json.dumps(filled(library.instantiate(library.builtin_templates()["message"])))
        )
        init_run(h.run, sp)
        before = (h.run / "spec.json").read_text()
        library.promote(
            private_library, candidate(private_library, rewrite(**{"5": "a different anchor"}))
        )
        assert (h.run / "spec.json").read_text() == before
        assert "a different anchor" not in json.dumps(load_frozen(h.run))
        paths = write_briefs(h.run, 1, h.files["cand"], h.files["ctrl"], None, False)
        assert "a different anchor" not in paths[0].read_text()


class TestTemplateReference:
    def write(self, h: Harness, ref: dict[str, Any], change: bool = False) -> Path:
        spec = filled(library.instantiate(library.builtin_templates()["message"]))
        spec["template"] = ref
        if change:
            spec["dimensions"][0]["anchors"]["5"] = "my own five"
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(spec))
        return sp

    def test_an_unknown_template_or_version_is_refused(self, h: Harness) -> None:
        with pytest.raises(EvalError, match="no known template"):
            init_run(h.run, self.write(h, {"id": "villanelle", "version": 1}))
        with pytest.raises(EvalError, match="has no version 7"):
            init_run(h.run, self.write(h, {"id": "message", "version": 7}))

    def test_the_hash_is_computed_and_modified_never_trusted(self, h: Harness) -> None:
        init_run(h.run, self.write(h, {"id": "message", "version": 1, "modified": False}, True))
        ref = load_frozen(h.run)["template"]
        assert ref["modified"] is True
        assert ref["sha256"] == library.dimensions_sha(
            library.builtin_templates()["message"]["dimensions"]
        )

    def test_a_forged_hash_is_replaced(self, h: Harness) -> None:
        init_run(h.run, self.write(h, {"id": "message", "version": 1, "sha256": "f" * 64}))
        ref = load_frozen(h.run)["template"]
        assert ref["sha256"] != "f" * 64 and ref["modified"] is False


class TestLibraryGuardrails:
    def test_a_refused_library_leaves_no_run(self, tmp_path: Path) -> None:
        sp = tmp_path / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        inside = Path(library.__file__).resolve().parent / "rubrics" / "mine"
        with pytest.raises(EvalError, match="inside the package"):
            init_run(tmp_path / "run", sp, library=inside)
        assert not (tmp_path / "run").exists()

    def test_inside_the_package_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inside = Path(library.__file__).resolve().parent / "rubrics" / "mine"
        monkeypatch.setenv("SEALED_PANEL_RUBRICS", str(inside))
        with pytest.raises(EvalError, match="inside the package"):
            library_dir()

    def test_inside_a_git_tree_needs_an_ignore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        with pytest.raises(EvalError, match="not ignored"):
            library_dir(repo / "lib")
        (repo / ".gitignore").write_text("/lib/\n")
        assert library_dir(repo / "lib") == repo / "lib"

    @pytest.mark.parametrize("value", ["rubrics", "./lib", "../lib"])
    def test_a_relative_environment_path_is_refused(
        self, value: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SEALED_PANEL_RUBRICS", value)
        with pytest.raises(EvalError, match="must be an absolute path"):
            library_dir()

    def test_a_relative_setting_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("SEALED_PANEL_RUBRICS")
        cfg_file = tmp_path / "c.toml"
        cfg_file.write_text('[library]\npath = "rubrics"\n')
        with pytest.raises(EvalError, match=r"library\.path must be an absolute path"):
            library_dir(cfg=load_config(cfg_file))

    def test_a_relative_flag_is_made_absolute(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert library_dir("lib") == tmp_path / "lib"

    def test_feedback_expands_the_library_flag(
        self, h: Harness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        monkeypatch.setenv("HOME", str(h.tmp / "home"))
        monkeypatch.chdir(h.tmp)
        assert main(["feedback", str(h.run), "--note", "too kind", "--library", "~/lib"]) == 0
        assert list((h.tmp / "home" / "lib" / "inbox").glob("c-*.json"))
        assert not (h.tmp / "~").exists()


class TestResolvedLibrary:
    def repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return repo

    def test_a_symlink_into_a_repository_is_refused(self, tmp_path: Path) -> None:
        repo = self.repo(tmp_path)
        (repo / "rubrics").mkdir()
        link = tmp_path / "outside" / "lib"
        link.parent.mkdir()
        link.symlink_to(repo / "rubrics")
        with pytest.raises(EvalError, match=f"{repo.resolve() / 'rubrics'}"):
            library_dir(link)

    def test_an_inherited_git_dir_does_not_answer_for_the_repository(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self.repo(tmp_path)
        other = tmp_path / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        (other / ".git" / "info").mkdir(exist_ok=True)
        (other / ".git" / "info" / "exclude").write_text("*\n")  # the other repo ignores all
        monkeypatch.setenv("GIT_DIR", str(other / ".git"))  # as inside a git hook
        with pytest.raises(EvalError, match="inside the git working tree"):
            library_dir(repo / "rubrics")

    @staticmethod
    def user_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home_ignores: bool) -> Path:
        """A throwaway user: HOME and XDG_CONFIG_HOME in tmp_path, never the real ones. When
        `home_ignores`, ~/.gitconfig names an excludes file that ignores `rubrics/`."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
        excludes = tmp_path / "excludes"
        excludes.write_text("rubrics/\n")
        if home_ignores:
            (home / ".gitconfig").write_text(f"[core]\n\texcludesFile = {excludes}\n")
        return excludes

    def test_the_users_git_config_global_is_obeyed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        excludes = self.user_git(tmp_path, monkeypatch, home_ignores=False)
        repo = self.repo(tmp_path)
        effective = tmp_path / "global.gitconfig"
        effective.write_text(f"[core]\n\texcludesFile = {excludes}\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(effective))
        assert library_dir(repo / "rubrics") == (repo / "rubrics").resolve()

    def test_a_home_config_overridden_by_git_config_global_does_not_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.user_git(tmp_path, monkeypatch, home_ignores=True)
        repo = self.repo(tmp_path)
        effective = tmp_path / "global.gitconfig"
        effective.write_text("[user]\n\tname = nobody\n")  # no excludes file
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(effective))
        with pytest.raises(EvalError, match="inside the git working tree"):
            library_dir(repo / "rubrics")

    def test_the_users_default_ignore_file_never_decides_a_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user_ignore = Path(os.environ["XDG_CONFIG_HOME"]) / "git" / "ignore"
        user_ignore.parent.mkdir(parents=True)
        user_ignore.write_text("*\n")  # where git looks by default: it ignores everything
        repo = self.repo(tmp_path)
        with pytest.raises(EvalError, match="not ignored"):
            library_dir(repo / "rubrics")

    @staticmethod
    def ignoring_repo(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / ".gitignore").write_text("rubrics/\n")  # git would say: ignored
        return repo

    @pytest.fixture
    def fresh_git_vars(self) -> Iterator[None]:
        config._git_local_env_vars.cache_clear()
        yield
        config._git_local_env_vars.cache_clear()

    @pytest.mark.usefixtures("fresh_git_vars")
    def test_no_git_on_the_path_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self.ignoring_repo(tmp_path)
        empty = tmp_path / "no-git"
        empty.mkdir()
        monkeypatch.setenv("PATH", str(empty))
        with pytest.raises(EvalError, match="not ignored"):
            library_dir(repo / "rubrics")

    @pytest.mark.skipif(os.name == "nt", reason="the stub git is a shell script")
    @pytest.mark.usefixtures("fresh_git_vars")
    def test_a_failing_local_env_vars_query_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self.ignoring_repo(tmp_path)
        real = shutil.which("git")
        assert real
        stub = tmp_path / "stub" / "git"
        stub.parent.mkdir()
        stub.write_text(f'#!/bin/sh\n[ "$1" = rev-parse ] && exit 1\nexec "{real}" "$@"\n')
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", str(stub.parent))
        with pytest.raises(EvalError, match="not ignored"):
            library_dir(repo / "rubrics")
        config._git_local_env_vars.cache_clear()
        monkeypatch.setenv("PATH", str(Path(real).parent))
        assert library_dir(repo / "rubrics") == (repo / "rubrics").resolve()  # git: ignored

    def test_dot_dot_out_of_a_repository_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = self.repo(tmp_path)
        (repo / "sub").mkdir()
        monkeypatch.chdir(repo / "sub")
        assert library_dir("../../outside/lib") == (tmp_path / "outside" / "lib").resolve()


class TestHistoryIsNeverOverwritten:
    def test_an_unreadable_history_file_keeps_its_id(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        library.reject(private_library, cid, "no")
        damaged = private_library / "history" / f"{cid}.json"
        damaged.write_text("{damaged")
        again = candidate(private_library, rewrite())
        assert again == f"{cid}-2"
        library.reject(private_library, again, "still no")
        assert damaged.read_text() == "{damaged"

    def test_a_history_file_is_never_replaced(self, private_library: Path) -> None:
        cid = candidate(private_library, rewrite())
        (private_library / "history").mkdir(parents=True, exist_ok=True)
        (private_library / "history" / f"{cid}.json").write_text("{kept}")
        with pytest.raises(EvalError, match="never overwritten"):
            library.reject(private_library, cid, "no")
        with pytest.raises(EvalError, match="never overwritten"):
            library.promote(private_library, cid)
        assert (private_library / "history" / f"{cid}.json").read_text() == "{kept}"
        assert (private_library / "inbox" / f"{cid}.json").exists()

    def test_promote_refuses_a_closed_id_before_the_prompt(
        self, private_library: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cid = candidate(private_library, rewrite())
        (private_library / "history").mkdir(parents=True, exist_ok=True)
        (private_library / "history" / f"{cid}.json").write_text("{kept}")
        with pytest.raises(EvalError, match="never overwritten"):
            library.plan_promotion(private_library, cid)
        asked: list[str] = []

        def answer(prompt: str) -> str:
            asked.append(prompt)
            return cid

        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", answer)
        assert main(["rubrics", "promote", cid, "--library", str(private_library)]) != 0
        assert asked == []


class TestInitLibraryFlag:
    def test_init_reads_the_library_it_is_given(
        self, h: Harness, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        other = tmp_path / "other-lib"
        other.mkdir()
        mine = {**library.builtin_templates()["message"], "id": "team-note"}
        (other / "team-note.json").write_text(json.dumps(mine))
        out = tmp_path / "spec.json"
        assert (
            main(["rubrics", "use", "team-note", "--out", str(out), "--library", str(other)]) == 0
        )
        out.write_text(json.dumps(filled(json.loads(out.read_text()))))
        assert main(["init", str(h.run), "--spec", str(out)]) == 2
        err = capsys.readouterr().err
        assert "names no known template" in err and "library" in err
        assert main(["init", str(h.run), "--spec", str(out), "--library", str(other)]) == 0
        frozen = json.loads((h.run / "config.json").read_text())["library"]["path"]
        assert frozen == str(other.resolve())
