"""Learning: candidates written to the inbox after a sealed panel, never a template change."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from conftest import BAD, FLAT, Harness, judge_json, make_spec
from sealed_panel import library
from sealed_panel.aggregate import aggregate_round
from sealed_panel.cli import main
from sealed_panel.config import load_config
from sealed_panel.errors import EvalError
from sealed_panel.learn import proposals_after_seal, user_disagreement
from sealed_panel.record import record_reply
from sealed_panel.spec import init_run

THREE = {"clarity": 3, "evidence": 3}


def kinds(lib: Path) -> list[tuple[str, str, int]]:
    return sorted((c["kind"], c["dimension"], c["count"]) for c in library.open_candidates(lib))


def sealed_mixed(
    h: Harness, per_judge: list[dict[str, Any]], scores: dict[str, Any]
) -> dict[str, Any]:
    """Brief a sealed panel and record each judge with its own judge_json options."""
    h.brief(1, "cand", sealed=True)
    for meta, kw in zip(h.keys(1, True)["judges"], per_judge, strict=True):
        sc = {r: scores.get(r, scores["candidate"]) for r in meta["key"].values()}
        raw = judge_json(meta, h.texts(1, meta, True), sc, **kw)
        record_reply(h.run, 1, meta["judge"], f"s{meta['judge']}", raw, True)
    return aggregate_round(h.run, 1, True)


class TestSharpenAnchor:
    def test_two_judges_flagging_one_dimension(self, h: Harness, private_library: Path) -> None:
        h.init()
        e = h.full(1, "cand", FLAT, sealed=True, severity={"clarity": "material"})
        assert kinds(private_library) == [("sharpen_anchor", "clarity", 1)]
        assert len(e["inbox"]) == 1
        c = library.open_candidates(private_library)[0]
        assert c["evidence"][0]["judges"] == [1, 2, 3]
        assert all(q for q in c["evidence"][0]["quotes"])
        assert c["proposed_change"]["action"] == "rewrite_anchors"

    def test_one_judge_is_not_enough(self, h: Harness, private_library: Path) -> None:
        h.init()
        sealed_mixed(
            h, [{"severity": {"clarity": "material"}}, {}, {}], {"candidate": FLAT, "control": BAD}
        )
        assert kinds(private_library) == []

    def test_loop_and_seal_evidence_share_one_candidate(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT, severity={"clarity": "blocking"})
        h.full(1, "cand", FLAT, sealed=True, severity={"clarity": "material"})
        assert kinds(private_library) == [("sharpen_anchor", "clarity", 2)]


class TestNewDimension:
    def test_one_sealed_judge_is_enough(self, h: Harness, private_library: Path) -> None:
        h.init()
        sealed_mixed(h, [{"wdim": "tone"}, {}, {}], {"candidate": FLAT, "control": BAD})
        assert kinds(private_library) == [("new_dimension", "tone", 1)]
        c = library.open_candidates(private_library)[0]
        assert c["proposed_change"]["dimension"]["id"] == "tone"

    def test_two_loop_judges_are_needed(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.brief(1, "cand")
        for n, meta in enumerate(h.keys(1)["judges"]):
            raw = judge_json(
                meta,
                h.texts(1, meta),
                {"candidate": FLAT, "control": BAD},
                wdim="tone" if n == 0 else "clarity",
            )
            record_reply(h.run, 1, meta["judge"], f"l{n}", raw, False)
        aggregate_round(h.run, 1, False)
        h.full(1, "cand", FLAT, sealed=True)
        assert kinds(private_library) == []

    def test_two_loop_judges_suffice(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", FLAT, wdim="Tone")
        h.full(1, "cand", FLAT, sealed=True)
        assert kinds(private_library) == [("new_dimension", "tone", 3)]

    def test_a_known_dimension_name_is_not_new(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True, wdim="Evidence")
        assert kinds(private_library) == []


class TestNonDiscriminating:
    def test_a_dimension_that_never_moved(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=2)
        h.full(1, "cand", THREE)
        h.full(2, "v2", {"clarity": 3, "evidence": 4}, THREE)
        h.full(1, "v2", FLAT, sealed=True)
        assert kinds(private_library) == [("non_discriminating", "clarity", 1)]
        ev = library.open_candidates(private_library)[0]["evidence"][0]
        assert ev["medians"] == [3, 3]

    def test_one_loop_round_is_not_enough(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=1)
        h.full(1, "cand", THREE)
        h.full(1, "v2", FLAT, sealed=True)
        assert kinds(private_library) == []

    def test_nothing_moved_at_all(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=2)
        h.full(1, "cand", THREE)
        h.full(2, "v2", THREE, THREE)
        h.full(1, "v2", FLAT, sealed=True)
        assert kinds(private_library) == []


class TestUserDisagreement:
    def test_needs_a_sealed_run(self, h: Harness) -> None:
        h.init()
        h.full(1, "cand", THREE)
        with pytest.raises(EvalError, match="seal the run first"):
            user_disagreement(h.run, "too kind")

    def test_with_and_without_a_dimension(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        with pytest.raises(EvalError, match="no dimension"):
            user_disagreement(h.run, "x", "ghost")
        with pytest.raises(EvalError, match="--note"):
            user_disagreement(h.run, "  ")
        user_disagreement(h.run, "the evidence reads thinner than 4", "evidence")
        user_disagreement(h.run, "it misses the tone entirely")
        assert kinds(private_library) == [
            ("user_disagreement", "(none)", 1),
            ("user_disagreement", "evidence", 1),
        ]
        c = next(
            c for c in library.open_candidates(private_library) if c["dimension"] == "evidence"
        )
        assert c["evidence"][0]["sealed_median"] == 4
        assert c["proposed_change"]["action"] == "rewrite_anchors"

    def test_the_same_disagreement_is_counted_not_duplicated(
        self, h: Harness, private_library: Path
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        user_disagreement(h.run, "first read", "evidence")
        user_disagreement(h.run, "second read", "evidence")
        assert kinds(private_library) == [("user_disagreement", "evidence", 2)]
        assert len(list((private_library / "inbox").glob("*.json"))) == 1

    def test_feedback_command(
        self, h: Harness, private_library: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True)
        rc = main(["feedback", str(h.run), "--note", "too kind", "--dimension", "clarity"])
        assert rc == 0
        assert "recorded your read as candidate" in capsys.readouterr().out


class TestGuards:
    def test_two_runs_on_one_template_share_a_candidate(
        self, tmp_path: Path, private_library: Path
    ) -> None:
        for name in ("a", "b"):
            (tmp_path / name).mkdir()
            h = Harness(tmp_path / name)
            h.init()
            h.full(1, "cand", FLAT, sealed=True, severity={"evidence": "material"})
        assert kinds(private_library) == [("sharpen_anchor", "evidence", 2)]

    def test_learning_never_writes_a_template(self, h: Harness, private_library: Path) -> None:
        h.init(max_rounds=2)
        h.full(1, "cand", THREE, wdim="tone")
        h.full(2, "v2", {"clarity": 3, "evidence": 4}, THREE, severity={"evidence": "material"})
        h.full(1, "v2", FLAT, sealed=True, severity={"evidence": "material"}, wdim="tone")
        assert {k for k, _, _ in kinds(private_library)} == {
            "new_dimension",
            "sharpen_anchor",
            "non_discriminating",
        }
        assert sorted(p.name for p in private_library.iterdir()) == ["inbox"]
        assert library.builtin_templates()["message"]["version"] == 1

    def test_learning_can_be_switched_off(self, h: Harness, private_library: Path) -> None:
        cfg_file = h.tmp / "c.toml"
        cfg_file.write_text("[library]\nlearn = false\n")
        sp = h.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec()))
        init_run(h.run, sp, load_config(cfg_file))
        e = h.full(1, "cand", FLAT, sealed=True, severity={"clarity": "material"})
        assert e["inbox"] == []
        assert not private_library.exists()

    def test_proposals_write_nothing(self, h: Harness, private_library: Path) -> None:
        h.init()
        h.full(1, "cand", FLAT, sealed=True, severity={"clarity": "material"})
        for p in sorted((private_library / "inbox").glob("*.json")):
            p.unlink()
        assert len(proposals_after_seal(h.run)) == 1
        assert list((private_library / "inbox").glob("*.json")) == []

    def test_aggregate_reports_the_inbox(
        self, h: Harness, capsys: pytest.CaptureFixture[str]
    ) -> None:
        h.init()
        h.play(
            1,
            "cand",
            {"candidate": FLAT, "control": BAD},
            sealed=True,
            severity={"clarity": "material"},
        )
        assert main(["aggregate", str(h.run), "--sealed"]) == 0
        assert "1 rubric candidate(s) in the library inbox" in capsys.readouterr().out
