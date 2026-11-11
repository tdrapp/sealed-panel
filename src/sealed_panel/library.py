"""The rubric library: built-in templates, the user library, suggestions and promotions.

Built-in templates are package data in `sealed_panel/rubrics/`. The user library is a
user-level directory (see `config.library_dir`) that holds user templates (`*.json`, which
override a built-in of the same id), `inbox/` (learned candidates) and `history/` (promoted or
rejected candidates). Candidates quote real texts, so the user library is private by nature
and never part of the package.

Nothing here changes a template on its own: only `promote`, run by a person through the CLI
after seeing the change side by side, writes a user template.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import textwrap
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from .config import writable_library
from .errors import EvalError
from .spec import REQUIRED_ANCHORS, TODO_MARKER, dimensions_sha, todo_paths, validate_spec
from .store import dump_json, load_json, refuse_links

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
OPTIONAL_TEMPLATE_KEYS = frozenset({"aliases", "facts_hint", "based_on"})
DIMENSION_KEYS = frozenset({"id", "name", "question", "anchors"})
PROVENANCE_KEYS = frozenset({"date", "source", "change"})
MIN_DIMENSIONS, MAX_DIMENSIONS = 3, 6
CANDIDATE_KINDS = ("judges_disagree", "control_too_close", "user_disagreement")
ACTIONS = ("add_dimension", "rewrite_anchors", "remove_dimension")
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
# A candidate id, checked before it becomes part of a path. The id inside a file is never used.
CANDIDATE_ID = re.compile(r"^c-[0-9a-f]{12}(?:-[0-9]{1,4})?$")
COLUMN = 44

Served = dict[str, Any]  # {"template": ..., "source": ..., "path": Path | None}


# -- templates ---------------------------------------------------------------------------


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def template_errors(t: Any) -> list[str]:
    """Every way a template breaks the format. An empty list means it is valid."""
    if not isinstance(t, dict):
        return ["a template must be a JSON object"]
    errors = [f"unknown key {k!r}" for k in sorted(set(t) - TEMPLATE_KEYS - OPTIONAL_TEMPLATE_KEYS)]
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
    if "aliases" in t and not _strs(t["aliases"]):
        errors.append("aliases must be a list of strings")
    if "facts_hint" in t and (not isinstance(t["facts_hint"], str) or not t["facts_hint"].strip()):
        errors.append("facts_hint must be a non-empty string")
    based = t.get("based_on")
    if based is not None and (
        not isinstance(based, dict)
        or set(based) != {"version"}
        or isinstance(based["version"], bool)
        or not isinstance(based["version"], int)
    ):
        errors.append("based_on must be an object with an integer version")
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
    """The packaged rubric templates, by id."""
    folder = resources.files("sealed_panel").joinpath("rubrics")
    found = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            t = _checked(json.loads(entry.read_text(encoding="utf-8")), f"built-in {entry.name}")
            found[t["id"]] = t
    return found


def user_templates(lib: Path) -> tuple[dict[str, tuple[dict[str, Any], Path]], list[str]]:
    """Valid user templates by id with their file, and a warning for each file skipped."""
    found: dict[str, tuple[dict[str, Any], Path]] = {}
    warnings = []
    if lib.is_dir():
        for path in sorted(lib.glob("*.json")):
            if path.is_symlink():
                warnings.append(f"skipped {path}: a symbolic link")
                continue
            try:
                t = _checked(json.loads(path.read_text(encoding="utf-8")), str(path))
            except (OSError, ValueError, EvalError) as exc:
                warnings.append(f"skipped {path}: {str(exc).splitlines()[0]}")
                continue
            if t["id"] in found:
                warnings.append(f"skipped {path}: id {t['id']!r} is already served by another file")
                continue
            found[t["id"]] = (t, path)
    return found, warnings


def all_templates(lib: Path) -> tuple[dict[str, Served], list[str]]:
    """Every served template by id, with its source and file; a user template overrides a
    built-in. Unreadable or invalid user files are skipped with a warning."""
    builtin = builtin_templates()
    out: dict[str, Served] = {
        k: {"template": v, "source": "built-in", "path": None} for k, v in builtin.items()
    }
    users, warnings = user_templates(lib)
    for tid, (t, path) in users.items():
        source = "user"
        if tid in builtin:
            source = f"user (overrides built-in v{builtin[tid]['version']})"
            base = (t.get("based_on") or {}).get("version")
            if base is not None and builtin[tid]["version"] > base:
                source += f"; built-in is newer than your base (v{base})"
        out[tid] = {"template": t, "source": source, "path": path}
    return dict(sorted(out.items())), warnings


def get_template(lib: Path, tid: str) -> Served:
    """One template the library serves, with its source; refused when unknown.

    Args:
        lib: The user rubric library.
        tid: The template id.
    """
    found, _ = all_templates(lib)
    if tid not in found:
        raise EvalError(f"no template {tid!r}; `sealed-panel rubrics list` shows them")
    return found[tid]


# -- suggest -----------------------------------------------------------------------------


def normalise_kind(kind: str) -> str:
    """A kind of text in the form templates use: folded case, dashes for spaces.

    Args:
        kind: The kind as the user wrote it.
    """
    return re.sub(r"[\s_]+", "-", kind.strip().casefold())


def _keyword_hits(t: dict[str, Any], text: str) -> list[str]:
    low = text.casefold()
    return sorted(
        {k for k in t["keywords"] if re.search(r"\b" + re.escape(k.casefold()) + r"\b", low)}
    )


def suggest(lib: Path, kind: str, text: str = "", top: int = 3) -> list[dict[str, Any]]:
    """The templates whose kinds or aliases match `kind` (case, spaces and underscores
    ignored), ordered by keyword overlap with the text, then by id. Empty when none matches."""
    if top < 1:
        raise EvalError("--top must be at least 1")
    wanted = normalise_kind(kind)
    rows = []
    for tid, served in all_templates(lib)[0].items():
        t = served["template"]
        names = {normalise_kind(k) for k in [*t["kinds"], *t.get("aliases", [])]}
        if wanted not in names:
            continue
        hits = _keyword_hits(t, text) if text else []
        rows.append(
            {
                "id": tid,
                "version": t["version"],
                "source": served["source"],
                "when_to_use": t["when_to_use"],
                "dimensions": [(d["id"], d["question"]) for d in t["dimensions"]],
                "keywords": hits,
            }
        )
    rows.sort(key=lambda r: (-len(r["keywords"]), r["id"]))
    return rows[:top]


# -- use ---------------------------------------------------------------------------------


def _todo(text: str) -> str:
    return f"{TODO_MARKER}: {text}"


def instantiate(t: dict[str, Any], personas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A spec skeleton from a template. Title, purpose, facts and every suggested instrument
    threshold are TODO markers, and so are the persona descriptions unless personas are given;
    `init` refuses until they are filled."""
    if personas is None:
        personas = [
            {"id": p["id"], "description": _todo(f"adapt this placeholder: {p['description']}")}
            for p in t["personas"]
        ]
    measured = []
    for m in t["measured"]:
        row = dict(m)
        for bound in ("max", "min"):
            if bound in row:
                row[bound] = _todo(f"your {bound} (suggested {row[bound]})")
        measured.append(row)
    dims = [
        {"id": d["id"], "name": d["name"], "question": d["question"], "anchors": dict(d["anchors"])}
        for d in t["dimensions"]
    ]
    hint = t.get("facts_hint") or "the facts the judges may trust"
    return {
        "title": _todo("a neutral title; the judges see it"),
        "purpose": _todo("what a 5 makes happen"),
        "template": {"id": t["id"], "version": t["version"], "sha256": dimensions_sha(dims)},
        "personas": personas,
        "dimensions": dims,
        "facts": _todo(f"{hint}, or replace this key with facts_file"),
        "measured": measured,
    }


def write_skeleton(lib: Path, tid: str, out: Path, persona_file: Path | None = None) -> Path:
    """Write a spec skeleton from a template; its TODO markers stop `init` until filled.

    Args:
        lib: The user rubric library.
        tid: The template id.
        out: Where to write the spec; refused when it exists.
        persona_file: A JSON list of personas to put in the skeleton, or None.
    """
    if out.exists():
        raise EvalError(f"{out} already exists: choose another path or remove it first")
    t = get_template(lib, tid)["template"]
    personas = None
    if persona_file is not None:
        loaded = load_json(persona_file)
        if not isinstance(loaded, list) or not all(isinstance(p, dict) for p in loaded):
            raise EvalError(f"{persona_file} must hold a list of personas (id and description)")
        personas = loaded
    skeleton = instantiate(t, personas)
    problems = [
        e
        for e in validate_spec(skeleton)
        if TODO_MARKER not in e and "must be a finite number" not in e
    ]
    if problems:
        raise EvalError("the skeleton would not be a valid spec:\n  " + "\n  ".join(problems))
    dump_json(out, skeleton)
    return out


# -- candidates --------------------------------------------------------------------------


def inbox_dir(lib: Path) -> Path:
    """The library's inbox of open candidates.

    Args:
        lib: The user rubric library.
    """
    refuse_links(lib, lib / "inbox")
    return lib / "inbox"


def history_dir(lib: Path) -> Path:
    """The library's history of closed candidates.

    Args:
        lib: The user rubric library.
    """
    refuse_links(lib, lib / "history")
    return lib / "history"


def check_candidate_id(cid: str) -> str:
    """The candidate id, refused unless it has the candidate shape.

    Args:
        cid: The id to check.
    """
    if not CANDIDATE_ID.match(cid):
        raise EvalError(f"{cid!r} is not a candidate id (c- and 12 hex characters)")
    return cid


def _template_tag(template: dict[str, Any] | None, rubric_sha: str) -> str:
    if template and template.get("id"):
        modified = "+modified" if template.get("modified") else ""
        return f"{template['id']}@v{template.get('version')}{modified}"
    return f"run:{rubric_sha[:12]}"


def dedup_key(kind: str, template: dict[str, Any] | None, rubric_sha: str, dimension: str) -> str:
    """Kind, template id and version (or the rubric hash without a template), dimension."""
    return f"{kind}|{_template_tag(template, rubric_sha)}|{dimension.casefold()}"


def _family(kind: str, template: dict[str, Any] | None, rubric_sha: str, dimension: str) -> str:
    """The key without the template version: the same signal on any version."""
    tag = template["id"] if template and template.get("id") else f"run:{rubric_sha[:12]}"
    return f"{kind}|{tag}|{dimension.casefold()}"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _load_candidates(folder: Path) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not folder.is_dir():
        return rows, warnings
    for path in sorted(folder.glob("*.json")):
        if not CANDIDATE_ID.match(path.stem):
            warnings.append(f"skipped {path}: not a candidate file name")
            continue
        if path.is_symlink():
            warnings.append(f"skipped {path}: a symbolic link")
            continue
        try:
            c = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"skipped {path}: {exc}")
            continue
        if not isinstance(c, dict) or c.get("kind") not in CANDIDATE_KINDS:
            warnings.append(f"skipped {path}: not a candidate")
            continue
        c["id"] = path.stem  # the file name is the id; the id field inside is never trusted
        rows.append(c)
    return rows, warnings


def open_candidates(lib: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """The open candidates of the inbox, and a warning per unreadable file.

    Args:
        lib: The user rubric library.
    """
    rows, warnings = _load_candidates(inbox_dir(lib))
    return [c for c in rows if c.get("status") == "open"], warnings


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def add_candidate(lib: Path, proposal: dict[str, Any]) -> Path | None:
    """Write a candidate to the inbox, or add its new evidence to the open candidate with the
    same key. A key already closed (rejected, or promoted into a newer version) is not
    reopened: None is returned. Evidence already recorded is not added twice, so learning a
    run again changes nothing; every run that showed the signal is listed under `runs`."""
    writable_library(lib)
    kind = proposal["kind"]
    if kind not in CANDIDATE_KINDS:
        raise EvalError(f"unknown candidate kind {kind!r}")
    run = proposal["run"]
    template = run.get("template")
    key = dedup_key(kind, template, run["rubric_sha256"], proposal["dimension"])
    family = _family(kind, template, run["rubric_sha256"], proposal["dimension"])
    history, _ = _load_candidates(history_dir(lib))
    if any(c.get("key") == key and c.get("status") in ("rejected", "promoted") for c in history):
        return None
    this_run = {"rubric_sha256": run["rubric_sha256"], "run_dir": run.get("run_dir")}
    opened, _ = open_candidates(lib)
    existing = next((c for c in opened if c.get("key") == key), None)
    if existing is not None:
        new = [
            e for e in proposal["evidence"] if not any(_same(e, x) for x in existing["evidence"])
        ]
        existing["evidence"] += new
        existing["count"] = len(existing["evidence"])
        runs = existing.setdefault("runs", [])
        if this_run not in runs:
            runs.append(this_run)
        path = inbox_dir(lib) / f"{existing['id']}.json"
        dump_json(path, existing)
        return path
    base = "c-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    cid, n = base, 1
    while (inbox_dir(lib) / f"{cid}.json").exists() or (history_dir(lib) / f"{cid}.json").exists():
        n += 1
        cid = f"{base}-{n}"
    earlier = [
        {"template": c.get("template"), "reason": c.get("reason", "")}
        for c in history
        if c.get("family") == family and c.get("status") == "rejected"
    ]
    candidate: dict[str, Any] = {
        "id": cid,
        "key": key,
        "family": family,
        "created": _now(),
        "kind": kind,
        "status": "open",
        "template": template or None,
        "dimension": proposal["dimension"],
        "run": run,
        "runs": [this_run],
        "evidence": list(proposal["evidence"]),
        "count": len(proposal["evidence"]),
        "proposed_change": proposal["proposed_change"],
    }
    if earlier:
        candidate["rejected_before"] = earlier
    path = inbox_dir(lib) / f"{cid}.json"
    dump_json(path, candidate)
    return path


def closed_status(lib: Path, proposal: dict[str, Any]) -> str | None:
    """How the proposal's key was closed (rejected or promoted), or None if it is not closed."""
    run = proposal["run"]
    key = dedup_key(
        proposal["kind"], run.get("template"), run["rubric_sha256"], proposal["dimension"]
    )
    history, _ = _load_candidates(history_dir(lib))
    found = [c.get("status") for c in history if c.get("key") == key]
    return "promoted" if "promoted" in found else ("rejected" if found else None)


def _history_file(lib: Path, cid: str) -> Path:
    """The history file for a candidate. A history file is never overwritten, readable or not:
    its id stays reserved."""
    dest = history_dir(lib) / f"{cid}.json"
    if dest.exists():
        raise EvalError(f"{dest} already exists: a closed candidate is never overwritten")
    return dest


def _open_candidate(lib: Path, cid: str) -> dict[str, Any]:
    path = inbox_dir(lib) / f"{check_candidate_id(cid)}.json"
    refuse_links(path)
    if not path.exists():
        raise EvalError(f"no open candidate {cid!r} in {inbox_dir(lib)}")
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalError(f"candidate {cid!r} cannot be read: {exc}") from exc
    if not isinstance(c, dict) or c.get("status") != "open":
        raise EvalError(
            f"candidate {cid!r} is "
            f"{c.get('status') if isinstance(c, dict) else 'damaged'}, not open"
        )
    c["id"] = cid
    return c


def _used_fields(change: dict[str, Any]) -> dict[str, Any]:
    """The parts of a change its action uses: TODO markers elsewhere do not block it."""
    action = change.get("action")
    if action == "rewrite_anchors":
        return {"dimension": change.get("dimension"), "anchors": change.get("anchors")}
    return {"dimension": change.get("dimension")}


def apply_change(t: dict[str, Any], change: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The template with the change applied, and a one-line summary. Refuses an incomplete or
    inapplicable change."""
    action = change.get("action")
    if action not in ACTIONS:
        raise EvalError(f"unknown action {action!r}; one of {', '.join(ACTIONS)}")
    if todo_paths(_used_fields(change), "proposed_change"):
        raise EvalError(
            f"the proposed change still holds {TODO_MARKER} markers: edit the candidate's "
            "proposed_change in the inbox, then promote"
        )
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


def _lines_of(d: dict[str, Any] | None) -> list[tuple[str, str]]:
    if d is None:
        return []
    rows = [("name", str(d.get("name", ""))), ("question", str(d.get("question", "")))]
    rows += [(f"anchor {k}", str(v)) for k, v in sorted((d.get("anchors") or {}).items())]
    return rows


def side_by_side(current: dict[str, Any], updated: dict[str, Any]) -> list[str]:
    """The dimensions a change touches, current text and proposed text in two columns."""
    before = {d["id"]: d for d in current["dimensions"]}
    after = {d["id"]: d for d in updated["dimensions"]}
    out = [f"{'':12} {'current':<{COLUMN}} | proposed"]
    for did in [*before, *[k for k in after if k not in before]]:
        if _same(before.get(did), after.get(did)):
            continue
        out.append(f"dimension {did}")
        left, right = dict(_lines_of(before.get(did))), dict(_lines_of(after.get(did)))
        for label in [*left, *[k for k in right if k not in left]]:
            a = textwrap.wrap(left.get(label, "(none)"), COLUMN) or [""]
            b = textwrap.wrap(right.get(label, "(removed)"), COLUMN) or [""]
            for i in range(max(len(a), len(b))):
                tag = label if i == 0 else ""
                out.append(
                    f"  {tag:10} {a[i] if i < len(a) else '':<{COLUMN}} | "
                    f"{b[i] if i < len(b) else ''}"
                )
    return out


def plan_promotion(lib: Path, cid: str, into: str | None = None) -> dict[str, Any]:
    """Everything promote would write, without writing it. Every refusal comes from here, so
    it comes before the confirmation prompt."""
    writable_library(lib)
    c = _open_candidate(lib, cid)
    _history_file(lib, c["id"])  # a closed id is refused before the person is asked
    tid = into or (c.get("template") or {}).get("id")
    if not tid:
        raise EvalError("this candidate names no template: say which one with --into")
    _, warnings = user_templates(lib)
    for w in warnings:
        if w.startswith(f"skipped {lib / (tid + '.json')}:"):
            raise EvalError(
                f"{lib / (tid + '.json')} is not a valid template: fix or remove it first"
            )
    served = get_template(lib, tid)
    current = served["template"]
    updated, summary = apply_change(current, c["proposed_change"])
    builtin = builtin_templates().get(tid)
    versions = [current["version"]] + ([builtin["version"]] if builtin else [])
    updated["version"] = max(versions) + 1
    if served["path"] is None and builtin is not None:
        updated["based_on"] = {"version": builtin["version"]}
    updated["provenance"] = [
        *current["provenance"],
        {
            "date": _now()[:10],
            "source": f"candidate {cid} ({c['kind']}, {c['count']} piece(s) of evidence)",
            "change": summary,
        },
    ]
    _checked(updated, f"template {tid} after the change")
    target = served["path"] or lib / f"{tid}.json"
    if served["path"] is None and target.exists():
        raise EvalError(
            f"{target} already holds a template of another id: rename it, or promote with "
            "--into that id"
        )
    return {"candidate": c, "id": tid, "target": target, "current": current, "updated": updated}


def apply_promotion(lib: Path, plan: dict[str, Any]) -> Path:
    """Write the template and close the candidate, all or nothing: both files are staged
    first, then moved into place; if the second move fails, the first is rolled back, and no
    staged file is left behind."""
    writable_library(lib)
    c, target = plan["candidate"], Path(plan["target"])
    c.update(
        {
            "status": "promoted",
            "closed": _now(),
            "promoted_into": {"id": plan["id"], "version": plan["updated"]["version"]},
        }
    )
    history = _history_file(lib, c["id"])  # again: it may have appeared during the prompt
    staged_t = target.with_name(f".{target.name}.staged")
    staged_h = history.with_name(f".{history.name}.staged")
    before = target.read_bytes() if target.exists() else None
    try:
        dump_json(staged_t, plan["updated"])
        dump_json(staged_h, c)
        os.replace(staged_t, target)
        try:
            os.replace(staged_h, history)
        except OSError:
            if before is None:
                target.unlink()
            else:
                target.write_bytes(before)
            raise
    finally:
        for leftover in (staged_t, staged_h):
            leftover.unlink(missing_ok=True)
    (inbox_dir(lib) / f"{c['id']}.json").unlink()
    return target


def promote(lib: Path, cid: str, into: str | None = None) -> Path:
    """Apply a candidate to a user template and close it; a person decides this.

    Args:
        lib: The user rubric library.
        cid: The candidate id.
        into: The template to change; the candidate's own when None.
    """
    return apply_promotion(lib, plan_promotion(lib, cid, into))


def reject(lib: Path, cid: str, reason: str) -> Path:
    """Close a candidate as rejected, with the person's reason.

    Args:
        lib: The user rubric library.
        cid: The candidate id.
        reason: Why it is rejected.
    """
    if not reason.strip():
        raise EvalError("--reason is required: say why the candidate is rejected")
    writable_library(lib)
    c = _open_candidate(lib, cid)
    c.update({"status": "rejected", "closed": _now(), "reason": reason})
    dest = _history_file(lib, c["id"])
    dump_json(dest, c)
    (inbox_dir(lib) / f"{c['id']}.json").unlink()
    return dest
