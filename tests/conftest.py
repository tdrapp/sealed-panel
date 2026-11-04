"""Shared fixtures: a synthetic spec, synthetic texts, and a harness that plays judges."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import pytest

from sealed_panel.aggregate import aggregate_round
from sealed_panel.brief import write_briefs
from sealed_panel.decide import decide
from sealed_panel.record import record_reply
from sealed_panel.spec import init_run
from sealed_panel.store import round_dir

CAND = (
    "We cut onboarding from nine days to two by replacing the manual KYC review "
    "with a scored queue."
)
CTRL = "Our solution is innovative and creates synergies to deliver value to all stakeholders."
GOOD = "Fraud losses fell 31 percent in one quarter after the scored queue went live in Lyon."
V2 = "We cut onboarding from nine days to two: a scored queue replaced manual KYC review in Lyon."
V3 = "Onboarding fell from nine days to two in Lyon once a scored queue replaced manual KYC review."
FLAT = {"clarity": 4, "evidence": 4}
BAD = {"clarity": 1, "evidence": 1}


def make_spec(**over: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "title": "test pitch",
        "purpose": "convince a bank COO to pilot",
        "personas": [{"id": "coo", "description": "COO of a retail bank"}],
        "dimensions": [
            {
                "id": "clarity",
                "question": "Can the persona retell it?",
                "anchors": {
                    "1": "cannot retell",
                    "3": "retells with effort",
                    "5": "retells at once",
                },
            },
            {
                "id": "evidence",
                "question": "Are claims backed?",
                "anchors": {"1": "none", "3": "one number", "5": "every claim backed"},
                "weight": 2,
            },
        ],
        "judges_per_persona": 3,
        "max_rounds": 4,
    }
    s.update(over)
    return s


def judge_json(
    meta: dict[str, Any],
    texts: dict[str, str],
    scores: dict[str, dict[str, int]],
    *,
    severity: dict[str, str] | None = None,
    wdim: str = "clarity",
    bad_quote: bool = False,
) -> str:
    """severity: {dim: sev} applied to the candidate only; every other cell is 'none'."""
    items = {}
    for label, role in meta["key"].items():
        sc = scores[role]
        q = "this sentence is not in the item at all" if bad_quote else texts[role][:40]
        sev = (severity or {}) if role == "candidate" else {}
        items[label] = {
            "retell": f"x {secrets.token_hex(4)}",  # real judges never reply identically
            "dimensions": {
                did: {
                    "reasoning": "because",
                    "quote": q,
                    "severity": sev.get(did, "none"),
                    "score": s,
                }
                for did, s in sc.items()
            },
            "weakest_point": {"text": "w", "dimension": wdim, "quote": texts[role][:20]},
        }
    return "Here you go:\n```json\n" + json.dumps({"items": items}) + "\n```"


def strip_fence(raw: str) -> Any:
    return json.loads(raw.split("```json\n")[1].split("\n```")[0])


class Harness:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.run = tmp / "run"
        self.files: dict[str, Path] = {}
        for name, text in (("cand", CAND), ("ctrl", CTRL), ("good", GOOD), ("v2", V2), ("v3", V3)):
            p = tmp / f"{name}.md"
            p.write_text(text, encoding="utf-8")
            self.files[name] = p
        self.n = 0

    def init(self, **over: Any) -> str:
        sp = self.tmp / "spec.json"
        sp.write_text(json.dumps(make_spec(**over)), encoding="utf-8")
        return init_run(self.run, sp)

    def brief(
        self, rnd: int, artefact: str, sealed: bool = False, good: bool = False
    ) -> list[Path]:
        return write_briefs(
            self.run,
            rnd,
            self.files[artefact],
            self.files["ctrl"],
            self.files["good"] if good else None,
            sealed,
        )

    def keys(self, rnd: int, sealed: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(
            (round_dir(self.run, rnd, sealed) / "keys.json").read_text()
        )
        return out

    def texts(self, rnd: int, meta: dict[str, Any], sealed: bool = False) -> dict[str, str]:
        rdir = round_dir(self.run, rnd, sealed)
        return {r: (rdir / f"{r}.md").read_text() for r in meta["key"].values()}

    def answer(
        self,
        rnd: int,
        scores_by_role: dict[str, dict[str, int]],
        sealed: bool = False,
        judges: int | None = None,
        **kw: Any,
    ) -> list[dict[str, Any]]:
        keys = self.keys(rnd, sealed)
        out = []
        for meta in keys["judges"][: judges or len(keys["judges"])]:
            texts = self.texts(rnd, meta, sealed)
            sc = {
                r: scores_by_role.get(r, scores_by_role["candidate"]) for r in meta["key"].values()
            }
            self.n += 1
            out.append(
                record_reply(
                    self.run,
                    rnd,
                    meta["judge"],
                    f"agent{self.n}",
                    judge_json(meta, texts, sc, **kw),
                    sealed,
                )
            )
        return out

    def play(
        self,
        rnd: int,
        artefact: str,
        scores_by_role: dict[str, dict[str, int]],
        sealed: bool = False,
        judges: int | None = None,
        good: bool = False,
        **kw: Any,
    ) -> list[dict[str, Any]]:
        self.brief(rnd, artefact, sealed, good)
        return self.answer(rnd, scores_by_role, sealed, judges, **kw)

    def full(
        self,
        rnd: int,
        artefact: str,
        cand: dict[str, int],
        prev: dict[str, int] | None = None,
        sealed: bool = False,
        **kw: Any,
    ) -> dict[str, Any]:
        sc = {"candidate": cand, "control": BAD, "baseline": FLAT}
        if prev:
            sc["previous"] = prev
        self.play(rnd, artefact, sc, sealed, **kw)
        return aggregate_round(self.run, rnd, sealed)

    def decision(self) -> dict[str, Any]:
        return decide(
            json.loads((self.run / "spec.json").read_text()),
            json.loads((self.run / "ledger.json").read_text()),
        )


@pytest.fixture(autouse=True)
def private_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own user library, never ./rubrics/ in the working tree."""
    lib = tmp_path / "library"
    monkeypatch.setenv("SEALED_PANEL_RUBRICS", str(lib))
    return lib


@pytest.fixture(autouse=True)
def no_inherited_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A git hook exports GIT_DIR and friends; the tests' own `git init` must not obey them.
    Git also reads an empty global configuration and no system one, so the user's own
    excludes files never decide a test; a test that needs a user configuration sets its own."""
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def h(tmp_path: Path) -> Harness:
    return Harness(tmp_path)
