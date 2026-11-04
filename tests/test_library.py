"""The rubric library: template format, suggestions, skeletons, promotion and rejection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from conftest import Harness, make_spec
from sealed_panel import library
from sealed_panel.brief import write_briefs
from sealed_panel.errors import EvalError
from sealed_panel.spec import RUBRIC_KEYS, TODO_MARKER, init_run, load_frozen, rubric_hash
from sealed_panel.spec import validate_spec as validate

BUILTINS = (
    "cv",
    "cover-letter",
    "pitch",
    "message",
    "essay",
    "interview-answer",
    "plan-doc",
    "prompt-output",
)


def filled(value: Any) -> Any:
    """A skeleton with every TODO marker replaced by plain text."""
    if isinstance(value, str):
        return value.replace(TODO_MARKER + ": ", "").replace(TODO_MARKER, "filled")
    if isinstance(value, dict):
        return {k: filled(v) for k, v in value.items()}
    if isinstance(value, list):
        return [filled(v) for v in value]
    return value


def candidate(lib: Path, change: dict[str, Any], template: str | None = "message") -> str:
    proposal = {
        "kind": "sharpen_anchor",
        "dimension": change.get("dimension", "x")
        if isinstance(change.get("dimension"), str)
        else "x",
        "run": {
            "rubric_sha256": "0" * 64,
            "template": {"id": template, "version": 1} if template else None,
        },
        "evidence": [{"run": "0" * 64, "round": 1, "judges": [1, 2], "quotes": ["q"]}],
        "proposed_change": change,
    }
    return library.add_candidate(lib, proposal).stem


class TestTemplates:
    def test_every_builtin_is_valid(self) -> None:
        found = library.builtin_templates()
        assert sorted(found) == sorted(BUILTINS)
        for t in found.values():
            assert library.template_errors(t) == [], t["id"]

    @pytest.mark.parametrize("tid", BUILTINS)
    def test_every_builtin_instantiates_into_a_valid_spec(self, tid: str, tmp_path: Path) -> None:
        t = library.builtin_templates()[tid]
        skeleton = library.instantiate(t)
        assert any(TODO_MARKER in e for e in validate(skeleton))
        spec = filled(skeleton)
        assert validate(spec) == []
        sp = tmp_path / "spec.json"
        sp.write_text(json.dumps(spec))
        init_run(tmp_path / "run", sp)
        assert load_frozen(tmp_path / "run")["template"] == {"id": tid, "version": 1}

    @pytest.mark.parametrize(
        ("change", "message"),
        [
            ({"bogus": 1}, "unknown key"),
            ({"id": "Bad Id"}, "id must be lowercase"),
            ({"version": 0}, "version must be an integer"),
            ({"kinds": []}, "kinds must be a non-empty list"),
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
        assert any(
            "dimensions must be" in e
            for e in library.template_errors({**t, "dimensions": dims[:2]})
        )
        many = [{**dims[0], "id": f"d{i}"} for i in range(7)]
        assert any(
            "dimensions must be" in e for e in library.template_errors({**t, "dimensions": many})
        )
        twin = [dims[0], dims[0], dims[1]]
        assert "dimension ids must be unique" in library.template_errors({**t, "dimensions": twin})
        no5 = [{**dims[0], "anchors": {"1": "a", "3": "b"}}, dims[1], dims[2]]
        assert any(
            "anchors 1, 3 and 5" in e for e in library.template_errors({**t, "dimensions": no5})
        )
        odd = [{**dims[0], "extra": 1}, dims[1], dims[2]]
        assert any(
            "exactly id, name" in e for e in library.template_errors({**t, "dimensions": odd})
        )
        todo = [{**dims[0], "question": TODO_MARKER + ": x"}, dims[1], dims[2]]
        assert any("markers" in e for e in library.template_errors({**t, "dimensions": todo}))
        assert library.template_errors([]) == ["a template must be a JSON object"]
        assert any("missing key" in e for e in library.template_errors({"id": "x"}))

    def test_user_template_overrides_a_builtin(self, private_library: Path) -> None:
        t = {**library.builtin_templates()["message"], "version": 3, "title": "Team messages"}
        private_library.mkdir(parents=True)
        (private_library / "message.json").write_text(json.dumps(t))
        got, source = library.get_template(private_library, "message")
        assert got["title"] == "Team messages"
        assert source == "user (overrides built-in v1)"

    def test_an_invalid_user_template_is_refused(self, private_library: Path) -> None:
        private_library.mkdir(parents=True)
        (private_library / "broken.json").write_text('{"id": "broken"}')
        with pytest.raises(EvalError, match="not a valid template"):
            library.all_templates(private_library)

    def test_unknown_template_refused(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="no template"):
            library.get_template(private_library, "nope")

    def test_library_path_precedence(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        assert library.library_dir(tmp_path / "x") == tmp_path / "x"
        monkeypatch.setenv(library.ENV_VAR, str(tmp_path / "env"))
        assert library.library_dir() == tmp_path / "env"
        monkeypatch.delenv(library.ENV_VAR)
        assert library.library_dir() == Path("./rubrics/")


class TestSuggest:
    @pytest.mark.parametrize("tid", BUILTINS)
    def test_each_kind_puts_its_template_first(self, tid: str, private_library: Path) -> None:
        for kind in library.builtin_templates()[tid]["kinds"]:
            rows = library.suggest(private_library, kind)
            assert rows[0]["id"] == tid, kind
            assert rows == library.suggest(private_library, kind), "deterministic"

    def test_keywords_break_ties_then_ids(self, private_library: Path) -> None:
        rows = library.suggest(private_library, "unknown-kind", top=8)
        assert [r["id"] for r in rows] == sorted(BUILTINS)
        text = "We need the deadline: please reply before Friday and enable the service."
        rows = library.suggest(private_library, "unknown-kind", text, top=1)
        assert rows[0]["id"] == "message"
        assert "keyword(s) in the text: before, deadline, enable" in rows[0]["reason"]
        assert "does not match" in rows[0]["reason"]

    def test_kind_outweighs_keywords(self, private_library: Path) -> None:
        text = "please reply before the deadline to enable the service for the team"
        rows = library.suggest(private_library, "essay", text, top=2)
        assert [r["id"] for r in rows] == ["essay", "message"]
        assert rows[0]["score"] == library.KIND_WEIGHT

    def test_top_must_be_positive(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="--top"):
            library.suggest(private_library, "essay", top=0)


class TestUse:
    def test_skeleton_freezes_the_template(self, tmp_path: Path, private_library: Path) -> None:
        out = library.write_skeleton(private_library, "message", tmp_path / "spec.json")
        skeleton = json.loads(out.read_text())
        assert skeleton["template"] == {"id": "message", "version": 1}
        sp = tmp_path / "spec.json"
        with pytest.raises(EvalError, match="TODO"):
            init_run(tmp_path / "run", sp)
        sp.write_text(json.dumps(filled(skeleton)))
        init_run(tmp_path / "run", sp)
        spec = json.loads((tmp_path / "run" / "spec.json").read_text())
        spec["template"]["version"] = 2
        (tmp_path / "run" / "spec.json").write_text(json.dumps(spec))
        with pytest.raises(EvalError, match="rubric changed"):
            load_frozen(tmp_path / "run")

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
            json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert rubric_hash(spec) == old

    def test_a_template_reference_has_a_shape(self) -> None:
        assert "spec.template must be an object" in "\n".join(validate(make_spec(template="x")))
        bad = make_spec(template={"id": "message", "version": "1"})
        assert any("integer version" in e for e in validate(bad))
        extra = make_spec(template={"id": "message", "version": 1, "src": "x"})
        assert any("unknown key 'src'" in e for e in validate(extra))


class TestPromote:
    def test_promote_copies_a_builtin_and_bumps_the_version(self, private_library: Path) -> None:
        cid = candidate(
            private_library,
            {
                "action": "rewrite_anchors",
                "dimension": "ask",
                "anchors": {"5": "the ask is in the first line"},
            },
        )
        path = library.promote(private_library, cid)
        t = json.loads(path.read_text())
        assert path == private_library / "message.json"
        assert t["version"] == 2
        assert t["dimensions"][0]["anchors"]["5"] == "the ask is in the first line"
        assert t["provenance"][-1]["source"].startswith(f"candidate {cid}")
        assert library.builtin_templates()["message"]["version"] == 1, "built-ins never change"
        closed = json.loads((private_library / "history" / f"{cid}.json").read_text())
        assert closed["status"] == "promoted"
        assert closed["promoted_into"] == {"id": "message", "version": 2}
        assert not (private_library / "inbox" / f"{cid}.json").exists()
        with pytest.raises(EvalError, match="no open candidate"):
            library.promote(private_library, cid)

    def test_promote_refuses_todo_markers(self, private_library: Path) -> None:
        cid = candidate(
            private_library,
            {
                "action": "rewrite_anchors",
                "dimension": "ask",
                "anchors": {"5": TODO_MARKER + ": write it"},
            },
        )
        with pytest.raises(EvalError, match="edit the candidate"):
            library.promote(private_library, cid)
        assert not (private_library / "message.json").exists()

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
        ],
    )
    def test_inapplicable_changes_refused(
        self, private_library: Path, change: dict[str, Any], message: str
    ) -> None:
        cid = candidate(private_library, change)
        with pytest.raises(EvalError, match=message):
            library.promote(private_library, cid)

    def test_a_change_that_breaks_the_format_is_refused(self, private_library: Path) -> None:
        cid = candidate(private_library, {"action": "add_dimension", "dimension": {"id": "tone"}})
        with pytest.raises(EvalError, match="not a valid template"):
            library.promote(private_library, cid)

    def test_add_and_remove_dimensions(self, private_library: Path) -> None:
        new = {
            "id": "tone",
            "name": "Tone",
            "question": "Is the tone plain?",
            "anchors": {"1": "a", "3": "b", "5": "c"},
        }
        library.promote(
            private_library,
            candidate(private_library, {"action": "add_dimension", "dimension": new}),
        )
        cid = candidate(private_library, {"action": "remove_dimension", "dimension": "brevity"})
        t = json.loads(library.promote(private_library, cid).read_text())
        assert [d["id"] for d in t["dimensions"]] == ["ask", "relevance", "evidence", "tone"]
        assert t["version"] == 3

    def test_into_is_needed_without_a_template(self, private_library: Path) -> None:
        cid = candidate(
            private_library, {"action": "remove_dimension", "dimension": "brevity"}, None
        )
        with pytest.raises(EvalError, match="--into"):
            library.promote(private_library, cid)
        library.promote(private_library, cid, into="message")

    def test_reject_moves_with_the_reason(self, private_library: Path) -> None:
        cid = candidate(private_library, {"action": "remove_dimension", "dimension": "brevity"})
        with pytest.raises(EvalError, match="--reason"):
            library.reject(private_library, cid, " ")
        path = library.reject(private_library, cid, "the flaw was in the text, not the anchor")
        closed = json.loads(path.read_text())
        assert (closed["status"], closed["reason"]) == (
            "rejected",
            "the flaw was in the text, not the anchor",
        )
        assert library.open_candidates(private_library) == []
        with pytest.raises(EvalError, match="no open candidate"):
            library.reject(private_library, cid, "again")

    def test_a_closed_candidate_is_not_reopened(self, private_library: Path) -> None:
        change = {"action": "remove_dimension", "dimension": "brevity"}
        first = candidate(private_library, change)
        library.reject(private_library, first, "no")
        second = candidate(private_library, change)
        assert second == f"{first}-2"
        (private_library / "inbox" / f"{second}.json").write_text(
            json.dumps(
                {
                    **json.loads((private_library / "inbox" / f"{second}.json").read_text()),
                    "status": "promoted",
                }
            )
        )
        with pytest.raises(EvalError, match="not open"):
            library.promote(private_library, second)

    def test_unknown_candidate_kind_refused(self, private_library: Path) -> None:
        with pytest.raises(EvalError, match="unknown candidate kind"):
            library.add_candidate(private_library, {"kind": "guess", "run": {}, "dimension": "x"})


class TestFrozenRun:
    def test_a_promotion_does_not_change_a_frozen_run(
        self, h: Harness, private_library: Path
    ) -> None:
        sp = h.tmp / "spec.json"
        library.write_skeleton(private_library, "message", sp)
        sp.write_text(json.dumps(filled(json.loads(sp.read_text()))))
        init_run(h.run, sp)
        before = (h.run / "spec.json").read_text()
        cid = candidate(
            private_library,
            {
                "action": "rewrite_anchors",
                "dimension": "ask",
                "anchors": {"5": "a different anchor"},
            },
        )
        library.promote(private_library, cid)
        assert (h.run / "spec.json").read_text() == before
        spec = load_frozen(h.run)
        assert spec["template"] == {"id": "message", "version": 1}
        assert "a different anchor" not in json.dumps(spec)
        paths = write_briefs(h.run, 1, h.files["cand"], h.files["ctrl"], None, False)
        assert "a different anchor" not in paths[0].read_text()
