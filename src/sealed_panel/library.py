"""The rubric library: built-in templates, the user library, suggestions and promotions.

Built-in templates are package data in `sealed_panel/rubrics/`. The user library is a
directory (`[library] path`, or the SEALED_PANEL_RUBRICS environment variable) that holds user
templates (`<id>.json`, which override a built-in of the same id), `inbox/` (learned
candidates) and `history/` (promoted or rejected candidates). Candidates quote real texts, so
the user library is private by nature and never part of the package.

Nothing here changes a template on its own: only `promote`, called by a person through the
CLI, writes a user template.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .config import Config, resolve
from .errors import EvalError
from .spec import REQUIRED_ANCHORS, TODO_MARKER, todo_paths, validate_spec
from .store import dump_json, load_json

ENV_VAR = "SEALED_PANEL_RUBRICS"
TEMPLATE_KEYS = frozenset(
    {
        "id",
        "version",
        "title",
        "kinds",
        "keywords",
        "when_to_use",
        "personas",
        "dimensions",
        "measured",
        "provenance",
    }
)
DIMENSION_KEYS = frozenset({"id", "name", "question", "anchors"})
PROVENANCE_KEYS = frozenset({"date", "source", "change"})
MIN_DIMENSIONS, MAX_DIMENSIONS = 3, 6
# Ranking: an exact kind match outweighs any number of keyword matches.
KIND_WEIGHT = 100
CANDIDATE_KINDS = ("new_dimension", "sharpen_anchor", "non_discriminating", "user_disagreement")
ACTIONS = ("add_dimension", "rewrite_anchors", "remove_dimension")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


# -- templates ---------------------------------------------------------------------------


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def template_errors(t: Any) -> list[str]:
    """Every way a template breaks the format. An empty list means it is valid."""
    if not isinstance(t, dict):
        return ["a template must be a JSON object"]
    errors = [f"unknown key {k!r}" for k in sorted(set(t) - TEMPLATE_KEYS)]
    errors += [f"missing key {k!r}" for k in sorted(TEMPLATE_KEYS - set(t))]
    if errors:
        return errors
    if not isinstance(t["id"], str) or not ID_PATTERN.match(t["id"]):
        errors.append("id must be lowercase letters, digits and dashes")
    if isinstance(t["version"], bool) or not isinstance(t["version"], int) or t["version"] < 1:
        errors.append("version must be an integer >= 1")
    for key in ("title", "when_to_use"):
        if not isinstance(t[key], str) or not t[key].strip():
            errors.append(f"{key} must be a non-empty string")
    if not _strs(t["kinds"]) or not t["kinds"]:
        errors.append("kinds must be a non-empty list of strings")
    if not _strs(t["keywords"]):
        errors.append("keywords must be a list of strings")
    personas = t["personas"]
    if not isinstance(personas, list) or not personas:
        errors.append("personas must be a non-empty list")
    else:
        for p in personas:
            if not isinstance(p, dict) or set(p) != {"id", "description"}:
                errors.append("each persona needs exactly an id and a description")
    dims = t["dimensions"]
    if not isinstance(dims, list) or not MIN_DIMENSIONS <= len(dims) <= MAX_DIMENSIONS:
        errors.append(f"dimensions must be a list of {MIN_DIMENSIONS} to {MAX_DIMENSIONS}")
    else:
        ids = []
        for d in dims:
            if not isinstance(d, dict) or set(d) != DIMENSION_KEYS:
                errors.append("each dimension needs exactly id, name, question and anchors")
                continue
            ids.append(d["id"])
            anchors = d["anchors"]
            if not isinstance(anchors, dict) or not all(
                isinstance(anchors.get(lvl), str) and anchors[lvl].strip()
                for lvl in REQUIRED_ANCHORS
            ):
                errors.append(f"dimension {d['id']}: anchors 1, 3 and 5 must be non-empty")
        if len(set(ids)) != len(ids):
            errors.append("dimension ids must be unique")
    if not isinstance(t["measured"], list):
        errors.append("measured must be a list")
    prov = t["provenance"]
    if not isinstance(prov, list) or not prov:
        errors.append("provenance must be a non-empty list")
    else:
        for entry in prov:
            if not isinstance(entry, dict) or set(entry) != PROVENANCE_KEYS:
                errors.append("each provenance entry needs exactly date, source and change")
    if not errors and todo_paths(t, "template"):
        errors.append(f"a template must not hold {TODO_MARKER} markers")
    return errors


def _checked(t: Any, where: str) -> dict[str, Any]:
    errors = template_errors(t)
    if errors:
        raise EvalError(f"{where} is not a valid template:\n  " + "\n  ".join(errors))
    out: dict[str, Any] = t
    return out


def builtin_templates() -> dict[str, dict[str, Any]]:
    folder = resources.files("sealed_panel").joinpath("rubrics")
    found = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            t = _checked(json.loads(entry.read_text(encoding="utf-8")), f"built-in {entry.name}")
            found[t["id"]] = t
    return found


def library_dir(override: Path | None = None, cfg: Config | None = None) -> Path:
    """The user library: an explicit path, else the environment variable, else the setting."""
    if override is not None:
        return Path(override)
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return Path(str(resolve(cfg).library["path"]))


def user_templates(lib: Path) -> dict[str, dict[str, Any]]:
    found = {}
    if lib.is_dir():
        for path in sorted(lib.glob("*.json")):
            t = _checked(load_json(path), str(path))
            found[t["id"]] = t
    return found


def all_templates(lib: Path) -> dict[str, tuple[dict[str, Any], str]]:
    """Every template by id with its source; a user template overrides a built-in."""
    builtin = builtin_templates()
    out: dict[str, tuple[dict[str, Any], str]] = {k: (v, "built-in") for k, v in builtin.items()}
    for tid, t in user_templates(lib).items():
        source = "user"
        if tid in builtin:
            source = f"user (overrides built-in v{builtin[tid]['version']})"
        out[tid] = (t, source)
    return dict(sorted(out.items()))


def get_template(lib: Path, tid: str) -> tuple[dict[str, Any], str]:
    found = all_templates(lib)
    if tid not in found:
        raise EvalError(f"no template {tid!r}; `sealed-panel rubrics list` shows them")
    return found[tid]


# -- suggest -----------------------------------------------------------------------------


def _keyword_hits(t: dict[str, Any], text: str) -> list[str]:
    low = text.casefold()
    return sorted(
        {k for k in t["keywords"] if re.search(r"\b" + re.escape(k.casefold()) + r"\b", low)}
    )


def suggest(lib: Path, kind: str, text: str = "", top: int = 3) -> list[dict[str, Any]]:
    """Rank templates: an exact kind match first, then keyword overlap with the text, then id."""
    if top < 1:
        raise EvalError("--top must be at least 1")
    rows = []
    for tid, (t, source) in all_templates(lib).items():
        kind_match = kind.casefold() in {k.casefold() for k in t["kinds"]}
        hits = _keyword_hits(t, text) if text else []
        score = (KIND_WEIGHT if kind_match else 0) + len(hits)
        parts = [f"kind {kind!r} {'matches' if kind_match else 'does not match'}"]
        parts.append(
            f"{len(hits)} keyword(s) in the text" + (f": {', '.join(hits)}" if hits else "")
        )
        rows.append(
            {
                "id": tid,
                "version": t["version"],
                "source": source,
                "score": score,
                "reason": "; ".join(parts),
            }
        )
    rows.sort(key=lambda r: (-r["score"], r["id"]))
    return rows[:top]


# -- use ---------------------------------------------------------------------------------


def _todo(text: str) -> str:
    return f"{TODO_MARKER}: {text}"


def instantiate(t: dict[str, Any], personas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A spec skeleton from a template. Title, purpose and facts are TODO markers, and so are
    the persona descriptions unless personas are given; `init` refuses until they are filled."""
    if personas is None:
        personas = [
            {"id": p["id"], "description": _todo(f"adapt this placeholder: {p['description']}")}
            for p in t["personas"]
        ]
    return {
        "title": _todo("a neutral title; the judges see it"),
        "purpose": _todo("what a 5 makes happen"),
        "template": {"id": t["id"], "version": t["version"]},
        "personas": personas,
        "dimensions": [
            {
                "id": d["id"],
                "name": d["name"],
                "question": d["question"],
                "anchors": dict(d["anchors"]),
            }
            for d in t["dimensions"]
        ],
        "facts": _todo("the facts the judges may trust, or replace this key with facts_file"),
        "measured": [dict(m) for m in t["measured"]],
    }


def write_skeleton(lib: Path, tid: str, out: Path, persona_file: Path | None = None) -> Path:
    if out.exists():
        raise EvalError(f"{out} already exists: choose another path or remove it first")
    t, _ = get_template(lib, tid)
    personas = None
    if persona_file is not None:
        loaded = load_json(persona_file)
        if not isinstance(loaded, list) or not all(isinstance(p, dict) for p in loaded):
            raise EvalError(f"{persona_file} must hold a list of personas (id and description)")
        personas = loaded
    skeleton = instantiate(t, personas)
    problems = [e for e in validate_spec(skeleton) if TODO_MARKER not in e]
    if problems:
        raise EvalError("the skeleton would not be a valid spec:\n  " + "\n  ".join(problems))
    dump_json(out, skeleton)
    return out


# -- candidates --------------------------------------------------------------------------


def inbox_dir(lib: Path) -> Path:
    return lib / "inbox"


def history_dir(lib: Path) -> Path:
    return lib / "history"


def dedup_key(kind: str, template_id: str | None, dimension: str) -> str:
    return f"{kind}|{template_id or '-'}|{dimension.casefold()}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def open_candidates(lib: Path) -> list[dict[str, Any]]:
    folder = inbox_dir(lib)
    if not folder.is_dir():
        return []
    rows = [load_json(p) for p in sorted(folder.glob("*.json"))]
    return [r for r in rows if isinstance(r, dict) and r.get("status") == "open"]


def add_candidate(lib: Path, proposal: dict[str, Any]) -> Path:
    """Write a candidate to the inbox, or append its evidence to the open candidate with the
    same kind, template and dimension. Returns the candidate's path."""
    kind = proposal["kind"]
    if kind not in CANDIDATE_KINDS:
        raise EvalError(f"unknown candidate kind {kind!r}")
    template = proposal["run"].get("template") or {}
    key = dedup_key(kind, template.get("id"), proposal["dimension"])
    existing = next((c for c in open_candidates(lib) if c.get("key") == key), None)
    if existing is not None:
        existing["evidence"] += proposal["evidence"]
        existing["count"] = len(existing["evidence"])
        path = inbox_dir(lib) / f"{existing['id']}.json"
        dump_json(path, existing)
        return path
    base = "c-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    cid, n = base, 1
    while (inbox_dir(lib) / f"{cid}.json").exists() or (history_dir(lib) / f"{cid}.json").exists():
        n += 1
        cid = f"{base}-{n}"
    candidate = {
        "id": cid,
        "key": key,
        "created": _now(),
        "kind": kind,
        "status": "open",
        "template": template or None,
        "dimension": proposal["dimension"],
        "run": proposal["run"],
        "evidence": list(proposal["evidence"]),
        "count": len(proposal["evidence"]),
        "proposed_change": proposal["proposed_change"],
    }
    path = inbox_dir(lib) / f"{cid}.json"
    dump_json(path, candidate)
    return path


def _open_candidate(lib: Path, cid: str) -> dict[str, Any]:
    path = inbox_dir(lib) / f"{cid}.json"
    if not path.exists():
        raise EvalError(f"no open candidate {cid!r} in {inbox_dir(lib)}")
    c: dict[str, Any] = load_json(path)
    if c.get("status") != "open":
        raise EvalError(f"candidate {cid!r} is {c.get('status')}, not open")
    return c


def _close(lib: Path, c: dict[str, Any]) -> Path:
    dest = history_dir(lib) / f"{c['id']}.json"
    dump_json(dest, c)
    (inbox_dir(lib) / f"{c['id']}.json").unlink()
    return dest


def apply_change(t: dict[str, Any], change: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The template with the change applied, and a one-line summary. Refuses an incomplete or
    inapplicable change."""
    if todo_paths(change, "proposed_change"):
        raise EvalError(
            f"the proposed change still holds {TODO_MARKER} markers: edit the candidate's "
            "proposed_change in the inbox, then promote"
        )
    action = change.get("action")
    if action not in ACTIONS:
        raise EvalError(f"unknown action {action!r}; one of {', '.join(ACTIONS)}")
    out = json.loads(json.dumps(t))
    dims = out["dimensions"]
    ids = [d["id"] for d in dims]
    if action == "add_dimension":
        new = change.get("dimension")
        if not isinstance(new, dict) or new.get("id") in ids:
            raise EvalError("add_dimension needs a new dimension with an id not in the template")
        dims.append(new)
        summary = f"add dimension {new['id']}"
    else:
        did = change.get("dimension")
        if did not in ids:
            raise EvalError(f"the template has no dimension {did!r}")
        if action == "remove_dimension":
            out["dimensions"] = [d for d in dims if d["id"] != did]
            summary = f"remove dimension {did}"
        else:
            anchors = change.get("anchors")
            if not isinstance(anchors, dict) or not anchors:
                raise EvalError("rewrite_anchors needs the new anchors")
            target = next(d for d in dims if d["id"] == did)
            target["anchors"] = {**target["anchors"], **anchors}
            summary = f"rewrite anchors {', '.join(sorted(anchors))} of {did}"
    return out, summary


def promote(lib: Path, cid: str, into: str | None = None) -> Path:
    """Apply an open candidate's proposed change to a user template (copying a built-in into
    the user library first), bump its version, append provenance, and close the candidate.
    Only a person runs this, through the CLI."""
    c = _open_candidate(lib, cid)
    tid = into or (c.get("template") or {}).get("id")
    if not tid:
        raise EvalError("this candidate names no template: say which one with --into")
    current, _ = get_template(lib, tid)
    updated, summary = apply_change(current, c["proposed_change"])
    versions = [current["version"]]
    builtin = builtin_templates().get(tid)
    if builtin is not None:
        versions.append(builtin["version"])
    updated["version"] = max(versions) + 1
    updated["provenance"] = [
        *current["provenance"],
        {
            "date": _now()[:10],
            "source": f"candidate {cid} ({c['kind']}, {c['count']} piece(s) of evidence)",
            "change": summary,
        },
    ]
    _checked(updated, f"template {tid} after the change")
    path = lib / f"{tid}.json"
    dump_json(path, updated)
    c.update(
        {
            "status": "promoted",
            "closed": _now(),
            "promoted_into": {"id": tid, "version": updated["version"]},
        }
    )
    _close(lib, c)
    return path


def reject(lib: Path, cid: str, reason: str) -> Path:
    if not reason.strip():
        raise EvalError("--reason is required: say why the candidate is rejected")
    c = _open_candidate(lib, cid)
    c.update({"status": "rejected", "closed": _now(), "reason": reason})
    return _close(lib, c)
