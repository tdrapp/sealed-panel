"""Learning: after a sealed panel, propose rubric changes as candidates in the user library.

Learning proposes and a person decides. Nothing here writes a template: candidates go to the
inbox, and only `sealed-panel rubrics promote`, run by a person, applies one. Each proposed
change holds TODO markers where a person must write the new text, and promotion refuses until
the fields its action uses are filled. Learning never fails a seal: an error is reported as
"learning skipped" and `sealed-panel learn RUN` tries again.

Kinds (rubric-level signals):
- judges_disagree: in the sealed panel, a dimension whose valid judges' scores spread by 2 or
  more, or a judge whose severity contradicts its score before capping (blocking with a 4 or
  5, material with a 5, none with a 1);
- control_too_close: in the sealed panel, a dimension where the control's raw median is
  within 1 point of the candidate's raw median;
- user_disagreement: the user's own read disagrees with the sealed grade (`feedback`).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from . import library
from .decide import decide
from .errors import EvalError
from .ledger import results_by_judge
from .spec import TODO_MARKER, load_frozen, run_config, run_library
from .store import load_json, round_dir

# A spread of scores across a dimension's valid sealed judges that shows they disagree.
DISAGREE_SPREAD = 2
# A control median within this many points of the candidate's does not discriminate. Both
# medians are taken from raw scores (before severity caps), in the sealed panel only, so a
# weak draft in the loop cannot trigger it.
CONTROL_GAP = 1


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
    run: Path, spec: dict[str, Any], sealed: dict[str, Any], info: dict[str, Any]
) -> list[dict[str, Any]]:
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
                if (
                    (sev == "blocking" and raw >= 4)
                    or (sev == "material" and raw >= 5)
                    or (sev == "none" and raw <= 1)
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


def _raw_median(results: list[dict[str, Any]], role: str, did: str) -> float | None:
    raw = [r["items"].get(role, {}).get("raw_scores", {}).get(did) for r in results]
    values = [float(x) for x in raw if x is not None]
    return statistics.median(values) if values else None


def _control_too_close(
    run: Path, spec: dict[str, Any], sealed: dict[str, Any], info: dict[str, Any]
) -> list[dict[str, Any]]:
    results = _valid(run, sealed["round"], sealed=True)  # the sealed panel only
    proposals = []
    for pid, p in sealed["personas"].items():
        mine = [r for r in results if r["persona"] == pid]
        for did in p["dimensions"]:
            cand, ctrl = _raw_median(mine, "candidate", did), _raw_median(mine, "control", did)
            if cand is None or ctrl is None or cand - ctrl > CONTROL_GAP:
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
                            "candidate_raw_median": cand,
                            "control_raw_median": ctrl,
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
    ledger = load_json(run / "ledger.json")
    sealed = [r for r in ledger["rounds"] if r.get("sealed")]
    if not sealed:
        raise EvalError("learning reads a sealed panel: seal the run first")
    info = _run_info(run, spec, ledger)
    return [
        *_judges_disagree(run, spec, sealed[-1], info),
        *_control_too_close(run, spec, sealed[-1], info),
    ]


def learn_after_seal(run: Path) -> list[Path]:
    """Write the candidates of a sealed run to the inbox. The learning switch is read by
    learn_safely, which is how aggregate and `sealed-panel learn` call this."""
    lib = run_library(run)
    paths = [library.add_candidate(lib, p) for p in proposals_after_seal(run)]
    return sorted({p for p in paths if p is not None})


def learn_safely(run: Path) -> tuple[list[Path], str]:
    """Learning after a seal: never raises. Returns the candidates and a status line."""
    try:
        if not run_config(run).library["learn"]:
            return [], "learning off"
        paths = learn_after_seal(run)
    except Exception as exc:  # learning must never fail a seal
        return [], f"learning skipped: {exc}; run `sealed-panel learn {run}` to try again"
    return paths, f"{len(paths)} rubric candidate(s) in {run_library(run) / 'inbox'}"


def user_disagreement(
    run: Path, note: str, dimension: str | None = None, lib: Path | None = None
) -> Path:
    """Record the user's own read disagreeing with the sealed grade, as a candidate."""
    if not note.strip():
        raise EvalError("--note is required: say where your read differs from the grade")
    spec = load_frozen(run)
    ledger = load_json(run / "ledger.json")
    d = decide(spec, ledger)
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
    path = library.add_candidate(lib or run_library(run), proposal)
    if path is None:
        raise EvalError("this disagreement was rejected before for this template version")
    return path
