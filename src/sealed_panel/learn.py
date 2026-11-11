"""Learning: after a sealed panel, propose rubric changes as candidates in the user library.

Learning proposes and a person decides. Nothing here writes a template: candidates go to the
inbox, and only `sealed-panel rubrics promote`, run by a person, applies one. Each proposed
change holds TODO markers where a person must write the new text, and promotion refuses until
the fields its action uses are filled. Learning runs only when asked, by `sealed-panel learning
learn RUN` on a sealed run: no aggregate calls it, and it only reads the run.

Kinds (rubric-level signals):
- judges_disagree: in the sealed panel, a dimension whose valid judges' scores spread by 2 or
  more, or a judge whose severity contradicts its score before capping (blocking with a 4 or
  5, material with a 5, none with a 1);
- control_too_close: in the sealed panel, a dimension where the candidate's mean raw score
  is at least CONTROL_MIN_CANDIDATE (4) and the control's mean raw score is within
  CONTROL_GAP (1) of it, both before severity caps;
- user_disagreement: the user's own read disagrees with the sealed grade (`feedback`).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from . import library
from .config import Config, library_dir
from .decide import BLOCKED, decide
from .errors import EvalError
from .ledger import results_by_judge
from .ledger import view as ledger_view
from .spec import TODO_MARKER, load_frozen, run_config
from .store import round_dir

# A spread of scores across a dimension's valid sealed judges that shows they disagree.
DISAGREE_SPREAD = 2
# control_too_close fires only when the sealed candidate's mean raw score on a dimension is at
# least this (a strong text) and the control's is within CONTROL_GAP of it: a low control next
# to a low candidate says nothing about the dimension. The 0.3.1 defaults of the settings.
CONTROL_MIN_CANDIDATE = 4.0
CONTROL_GAP = 1.0


def _todo(text: str) -> str:
    return f"{TODO_MARKER}: {text}"


def _valid(run: Path, rnd: int, sealed: bool) -> list[dict[str, Any]]:
    rdir = round_dir(run, rnd, sealed)
    return [
        next(r for r in rs if r["ok"])
        for rs in results_by_judge(rdir).values()
        if any(r["ok"] for r in rs)
    ]


def _quotes(results: list[dict[str, Any]], did: str) -> list[dict[str, Any]]:
    """Each judge's quote on the candidate for one dimension, and whether code verified it."""
    out = []
    for r in results:
        cand = r["items"].get("candidate", {})
        if did in cand.get("quotes", {}):
            out.append(
                {
                    "judge": r["judge"],
                    "quote": cand["quotes"][did],
                    "verified": did not in cand.get("unverified", []),
                }
            )
    return out


def _run_info(run: Path, spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    return {
        "rubric_sha256": ledger["rubric_sha256"],
        "template": spec.get("template"),
        "run_dir": str(run.absolute()),
    }


def _anchors_of(spec: dict[str, Any], did: str) -> dict[str, str]:
    found: dict[str, Any] = next((d for d in spec["dimensions"] if d["id"] == did), {})
    return dict(found.get("anchors", {}))


def _judges_disagree(
    run: Path,
    spec: dict[str, Any],
    sealed: dict[str, Any],
    info: dict[str, Any],
    cfg: Config,
) -> list[dict[str, Any]]:
    caps = cfg.severity_caps
    results = _valid(run, sealed["round"], True)
    proposals = []
    for pid, p in sealed["personas"].items():
        for did, e in p["dimensions"].items():
            notes = []
            spread = max(e["scores"]) - min(e["scores"])
            if spread >= DISAGREE_SPREAD:
                notes.append(f"scores spread by {spread:g} ({'/'.join(map(str, e['scores']))})")
            for r in results:
                if r["persona"] != pid:
                    continue
                cand = r["items"].get("candidate", {})
                raw, sev = cand.get("raw_scores", {}).get(did), cand.get("severity", {}).get(did)
                if raw is None or sev is None:
                    continue
                # A score above its severity's cap (the run's caps), or no flaw named on a 1.
                if (sev in ("blocking", "material") and raw > caps[sev]) or (
                    sev == "none" and raw <= 1
                ):
                    notes.append(f"judge {r['judge']} gave {raw} with severity {sev}")
            if not notes:
                continue
            proposals.append(
                {
                    "kind": "judges_disagree",
                    "dimension": did,
                    "run": info,
                    "evidence": [
                        {
                            "run": info["rubric_sha256"],
                            "run_dir": info["run_dir"],
                            "round": sealed["round"],
                            "sealed": True,
                            "persona": pid,
                            "scores": e["scores"],
                            "quotes": _quotes([r for r in results if r["persona"] == pid], did),
                            "note": "; ".join(notes),
                        }
                    ],
                    "proposed_change": {
                        "action": "rewrite_anchors",
                        "dimension": did,
                        "current_anchors": _anchors_of(spec, did),
                        "anchors": {
                            "1": _todo("evidence on the page for a 1, so judges agree"),
                            "3": _todo("evidence on the page for a 3"),
                            "5": _todo("evidence on the page for a 5"),
                        },
                    },
                }
            )
    return proposals


def _raw_mean(results: list[dict[str, Any]], role: str, did: str) -> float | None:
    raw = [r["items"].get(role, {}).get("raw_scores", {}).get(did) for r in results]
    values = [float(x) for x in raw if x is not None]
    return statistics.mean(values) if values else None


def _control_too_close(
    run: Path, spec: dict[str, Any], sealed: dict[str, Any], info: dict[str, Any]
) -> list[dict[str, Any]]:
    """A dimension that does not discriminate: the sealed panel scores a strong candidate and
    the bad control close together. A weak candidate never triggers it: a low candidate next to
    a low control says nothing about the dimension. Calibration bounds each judge's control
    mean over the persona's dimensions (control_max), not its score on one dimension, so a
    control can score 3 on one dimension and stay calibrated; docs/rubrics.md derives when."""
    results = _valid(run, sealed["round"], sealed=True)  # the sealed panel only
    proposals = []
    for pid, p in sealed["personas"].items():
        mine = [r for r in results if r["persona"] == pid]
        for did in p["dimensions"]:
            cand, ctrl = _raw_mean(mine, "candidate", did), _raw_mean(mine, "control", did)
            if cand is None or ctrl is None or cand < CONTROL_MIN_CANDIDATE:
                continue
            if cand - ctrl > CONTROL_GAP:
                continue
            proposals.append(
                {
                    "kind": "control_too_close",
                    "dimension": did,
                    "run": info,
                    "evidence": [
                        {
                            "run": info["rubric_sha256"],
                            "run_dir": info["run_dir"],
                            "round": sealed["round"],
                            "sealed": True,
                            "persona": pid,
                            "candidate_raw_mean": cand,
                            "control_raw_mean": ctrl,
                            "quotes": _quotes(mine, did),
                        }
                    ],
                    "proposed_change": {
                        "action": "rewrite_anchors",
                        "dimension": did,
                        "current_anchors": _anchors_of(spec, did),
                        "anchors": {
                            "1": _todo("describe what the bad version does on this dimension"),
                            "3": _todo("the middle, distinct from both the 1 and the 5"),
                        },
                    },
                }
            )
    return proposals


def proposals_after_seal(run: Path) -> list[dict[str, Any]]:
    """Every candidate the sealed run supports, before de-duplication. Writes nothing."""
    spec = load_frozen(run)
    ledger = ledger_view(run)
    sealed = [r for r in ledger["rounds"] if r.get("sealed")]
    if not sealed:
        raise EvalError("learning reads a sealed panel: seal the run first")
    info = _run_info(run, spec, ledger)
    cfg = run_config(run)
    return [
        *_judges_disagree(run, spec, sealed[-1], info, cfg),
        *_control_too_close(run, spec, sealed[-1], info),
    ]


def learn_after_seal(run: Path, lib: Path) -> list[Path]:
    """Write the candidates of a sealed run to the library's inbox; the run is only read.
    `sealed-panel learning learn RUN` calls this, and nothing else does.

    Args:
        run: The run directory, sealed.
        lib: The user library, resolved by library_dir.
    """
    paths = [library.add_candidate(lib, p) for p in proposals_after_seal(run)]
    return sorted({p for p in paths if p is not None})


def user_disagreement(
    run: Path, note: str, dimension: str | None = None, lib: Path | None = None
) -> Path:
    """Record the user's own read disagreeing with the sealed grade, as a candidate."""
    if not note.strip():
        raise EvalError("--note is required: say where your read differs from the grade")
    spec = load_frozen(run)
    ledger = ledger_view(run)
    d = decide(spec, ledger)
    if d["decision"] == BLOCKED:
        raise EvalError(
            "the sealed grade is blocked by an injection flag and does not stand: there is no "
            "grade to disagree with"
        )
    if d["decision"] != "SEALED":
        raise EvalError("feedback disagrees with a sealed grade: seal the run first")
    final = d["final"]
    dims = {did: e for p in final["personas"].values() for did, e in p["dimensions"].items()}
    if dimension is not None and dimension not in dims:
        raise EvalError(f"the rubric has no dimension {dimension!r}")
    results = _valid(run, final["round"], True)
    info = _run_info(run, spec, ledger)
    evidence: dict[str, Any] = {
        "run": info["rubric_sha256"],
        "run_dir": info["run_dir"],
        "round": final["round"],
        "sealed": True,
        "note": note,
        "judges": [r["judge"] for r in results],
    }
    if dimension is not None:
        evidence["sealed_median"] = dims[dimension]["median"]
        evidence["quotes"] = _quotes(results, dimension)
        change: dict[str, Any] = {
            "action": "rewrite_anchors",
            "dimension": dimension,
            "current_anchors": _anchors_of(spec, dimension),
            "anchors": {
                "3": _todo("what the user saw that the anchor misses"),
                "5": _todo("what the page shows for a 5, in the user's terms"),
            },
        }
    else:
        change = {
            "action": "add_dimension",
            "dimension": {
                "id": _todo("an id"),
                "name": _todo("what the user judged that the rubric does not"),
                "question": _todo("the question a judge answers"),
                "anchors": {
                    "1": _todo("evidence on the page for a 1"),
                    "3": _todo("evidence on the page for a 3"),
                    "5": _todo("evidence on the page for a 5"),
                },
            },
        }
    proposal = {
        "kind": "user_disagreement",
        "dimension": dimension or "(none)",
        "run": info,
        "evidence": [evidence],
        "proposed_change": change,
    }
    target = lib or library_dir()
    path = library.add_candidate(target, proposal)
    if path is None:
        if library.closed_status(target, proposal) == "promoted":
            raise EvalError(
                "this disagreement was already promoted into a newer version of the template: "
                "start a run from that version to record a new read"
            )
        raise EvalError("this disagreement was rejected before for this template version")
    return path
