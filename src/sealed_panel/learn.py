"""Learning: after a sealed panel, propose rubric changes as candidates in the user library.

Learning proposes and a person decides. Nothing here writes a template: candidates go to the
inbox, and only `sealed-panel rubrics promote`, run by a person, applies one. Each proposed
change holds TODO markers where a person must write the new text, and promotion refuses until
they are filled.

Kinds:
- new_dimension: a judge's weakest point names a dimension the rubric lacks, in the sealed
  panel, or for two or more judges across the loop;
- sharpen_anchor: two or more judges of one round give the same dimension a blocking or
  material finding;
- non_discriminating: over two or more loop rounds, a dimension's median never moved while
  another dimension's did;
- user_disagreement: the user's own read disagrees with the sealed grade (`feedback`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import library
from .config import Config
from .decide import decide
from .errors import EvalError
from .ledger import results_by_judge
from .spec import TODO_MARKER, load_frozen, run_config
from .store import load_json, round_dir

# Two or more loop judges must name the same missing dimension; one sealed judge is enough.
NEW_DIMENSION_LOOP_JUDGES = 2
# Judges of one round who flag the same dimension before an anchor is proposed for sharpening.
SHARPEN_JUDGES = 2
FLAGGED = ("blocking", "material")


def _todo(text: str) -> str:
    return f"{TODO_MARKER}: {text}"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-") or "new-dimension"


def _valid(run: Path, rnd: int, sealed: bool) -> list[dict[str, Any]]:
    rdir = round_dir(run, rnd, sealed)
    return [
        next(r for r in rs if r["ok"])
        for rs in results_by_judge(rdir).values()
        if any(r["ok"] for r in rs)
    ]


def _quote(result: dict[str, Any], did: str) -> str:
    return str(result["items"].get("candidate", {}).get("quotes", {}).get(did, ""))


def _run_info(spec: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    return {"rubric_sha256": ledger["rubric_sha256"], "template": spec.get("template")}


def _new_dimension(
    run: Path, spec: dict[str, Any], rounds: list[dict[str, Any]], info: dict[str, Any]
) -> list[dict[str, Any]]:
    known = {d["id"].casefold() for d in spec["dimensions"]}
    known |= {str(d.get("name", "")).casefold() for d in spec["dimensions"]}
    seen: dict[str, list[dict[str, Any]]] = {}
    for r in rounds:
        for res in _valid(run, r["round"], bool(r.get("sealed"))):
            wp = res["items"].get("candidate", {}).get("weakest_point", {})
            name = wp.get("dimension")
            if not isinstance(name, str) or not name.strip() or name.casefold() in known:
                continue
            seen.setdefault(name.casefold(), []).append(
                {
                    "round": r["round"],
                    "sealed": bool(r.get("sealed")),
                    "judge": res["judge"],
                    "name": name,
                    "quote": wp.get("quote", ""),
                    "text": wp.get("text", ""),
                }
            )
    proposals = []
    for key, hits in sorted(seen.items()):
        in_sealed = any(h["sealed"] for h in hits)
        loop_judges = {(h["round"], h["judge"]) for h in hits if not h["sealed"]}
        if not in_sealed and len(loop_judges) < NEW_DIMENSION_LOOP_JUDGES:
            continue
        name = hits[0]["name"]
        proposals.append(
            {
                "kind": "new_dimension",
                "dimension": key,
                "run": info,
                "evidence": [
                    {
                        "run": info["rubric_sha256"],
                        "round": h["round"],
                        "sealed": h["sealed"],
                        "judges": [h["judge"]],
                        "quotes": [h["quote"]],
                        "note": h["text"],
                    }
                    for h in hits
                ],
                "proposed_change": {
                    "action": "add_dimension",
                    "dimension": {
                        "id": _slug(name),
                        "name": name,
                        "question": _todo("the question a judge answers"),
                        "anchors": {
                            "1": _todo("evidence on the page for a 1"),
                            "3": _todo("evidence on the page for a 3"),
                            "5": _todo("evidence on the page for a 5"),
                        },
                    },
                },
            }
        )
    return proposals


def _anchors_of(spec: dict[str, Any], did: str) -> dict[str, str]:
    found: dict[str, Any] = next((d for d in spec["dimensions"] if d["id"] == did), {})
    return dict(found.get("anchors", {}))


def _sharpen_anchor(
    run: Path, spec: dict[str, Any], rounds: list[dict[str, Any]], info: dict[str, Any]
) -> list[dict[str, Any]]:
    by_dim: dict[str, list[dict[str, Any]]] = {}
    for r in rounds:
        results = {res["judge"]: res for res in _valid(run, r["round"], bool(r.get("sealed")))}
        for p in r["personas"].values():
            flagged: dict[str, list[dict[str, Any]]] = {}
            for f in p["findings"]:
                if f["severity"] in FLAGGED:
                    flagged.setdefault(f["dimension"], []).append(f)
            for did, fs in flagged.items():
                judges = sorted({f["judge"] for f in fs})
                if len(judges) < SHARPEN_JUDGES:
                    continue
                by_dim.setdefault(did, []).append(
                    {
                        "run": info["rubric_sha256"],
                        "round": r["round"],
                        "sealed": bool(r.get("sealed")),
                        "judges": judges,
                        "quotes": [_quote(results[j], did) for j in judges if j in results],
                        "note": "; ".join(f["text"] for f in fs),
                    }
                )
    return [
        {
            "kind": "sharpen_anchor",
            "dimension": did,
            "run": info,
            "evidence": evidence,
            "proposed_change": {
                "action": "rewrite_anchors",
                "dimension": did,
                "current_anchors": _anchors_of(spec, did),
                "anchors": {
                    "3": _todo("name the flaw the judges kept finding"),
                    "5": _todo("say what the page shows when that flaw is absent"),
                },
            },
        }
        for did, evidence in sorted(by_dim.items())
    ]


def _non_discriminating(
    run: Path, spec: dict[str, Any], loop: list[dict[str, Any]], info: dict[str, Any]
) -> list[dict[str, Any]]:
    # A median can only be seen not to move over two or more loop rounds: with one round,
    # no dimension has moved and nothing below is proposed.
    medians: dict[tuple[str, str], list[float]] = {}
    for r in loop:
        for pid, p in r["personas"].items():
            for did, e in p["dimensions"].items():
                medians.setdefault((pid, did), []).append(e["median"])
    moved = {k for k, v in medians.items() if len(set(v)) > 1}
    if not moved:
        return []
    last = loop[-1]
    results = _valid(run, last["round"], False)
    proposals = []
    for (pid, did), values in sorted(medians.items()):
        if (pid, did) in moved:
            continue
        proposals.append(
            {
                "kind": "non_discriminating",
                "dimension": did,
                "run": info,
                "evidence": [
                    {
                        "run": info["rubric_sha256"],
                        "rounds": [r["round"] for r in loop],
                        "persona": pid,
                        "medians": values,
                        "judges": [res["judge"] for res in results],
                        "quotes": [_quote(res, did) for res in results],
                        "note": f"median stayed at {values[0]:g} over {len(values)} rounds "
                        "while other dimensions moved",
                    }
                ],
                "proposed_change": {
                    "action": "rewrite_anchors",
                    "dimension": did,
                    "current_anchors": _anchors_of(spec, did),
                    "anchors": {
                        "1": _todo("evidence on the page for a 1"),
                        "3": _todo("evidence on the page for a 3"),
                        "5": _todo("evidence on the page for a 5"),
                    },
                    "alternative": "set action to remove_dimension to drop it instead",
                },
            }
        )
    return proposals


def proposals_after_seal(run: Path) -> list[dict[str, Any]]:
    """Every candidate the sealed run supports, before de-duplication. Writes nothing."""
    spec = load_frozen(run)
    ledger = load_json(run / "ledger.json")
    loop = [r for r in ledger["rounds"] if not r.get("sealed")]
    rounds = ledger["rounds"]
    info = _run_info(spec, ledger)
    return [
        *_new_dimension(run, spec, rounds, info),
        *_sharpen_anchor(run, spec, rounds, info),
        *_non_discriminating(run, spec, loop, info),
    ]


def learn_after_seal(run: Path, cfg: Config | None = None) -> list[Path]:
    """Write the candidates of a sealed run to the inbox; nothing when learning is off."""
    cfg = cfg or run_config(run)
    if not cfg.library["learn"]:
        return []
    lib = library.library_dir(cfg=cfg)
    paths = []
    for proposal in proposals_after_seal(run):
        paths.append(library.add_candidate(lib, proposal))
    return sorted(set(paths))


def user_disagreement(
    run: Path, note: str, dimension: str | None = None, lib: Path | None = None
) -> Path:
    """Record the user's own read disagreeing with the sealed grade, as a candidate."""
    if not note.strip():
        raise EvalError("--note is required: say where your read differs from the grade")
    spec = load_frozen(run)
    cfg = run_config(run)
    ledger = load_json(run / "ledger.json")
    d = decide(spec, ledger)
    if d["decision"] != "SEALED":
        raise EvalError("feedback disagrees with a sealed grade: seal the run first")
    final = d["final"]
    dims = {did: e for p in final["personas"].values() for did, e in p["dimensions"].items()}
    if dimension is not None and dimension not in dims:
        raise EvalError(f"the rubric has no dimension {dimension!r}")
    results = _valid(run, final["round"], True)
    info = _run_info(spec, ledger)
    evidence: dict[str, Any] = {
        "run": info["rubric_sha256"],
        "round": final["round"],
        "sealed": True,
        "note": note,
        "judges": [r["judge"] for r in results],
    }
    if dimension is not None:
        evidence["sealed_median"] = dims[dimension]["median"]
        evidence["quotes"] = [_quote(r, dimension) for r in results]
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
    return library.add_candidate(lib or library.library_dir(cfg=cfg), proposal)
