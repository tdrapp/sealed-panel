"""The spec: validate its shape and values, freeze it with the configuration, check the freeze."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .config import (
    MAX_JUDGES_PER_PERSONA,
    RUN_BOUNDS,
    Config,
    guard_library,
    library_dir,
    resolve,
    run_value_error,
)
from .errors import EvalError
from .store import dump_json, load_json, read_text, write_file
from .text import injection_flags

REQUIRED_ANCHORS = ("1", "3", "5")
ANCHOR_LEVELS = frozenset({"1", "2", "3", "4", "5"})
RUBRIC_KEYS = (
    "title",
    "purpose",
    "personas",
    "dimensions",
    "framings",
    "measured",
    "facts",
    "declared_choices",
    "judges_per_persona",
    "max_rounds",
    "control_max",
    "good_min",
    "noise_floor_min",
    "template",
)
# Keys hashed only when present, so a spec without them keeps the hash it had before.
OPTIONAL_HASH_KEYS = frozenset({"template"})
# A string a skeleton leaves for the user to fill; `init` refuses a spec that still holds one.
TODO_MARKER = "TODO(sealed-panel)"
TEMPLATE_REF_KEYS = frozenset({"id", "version", "sha256", "modified"})
SPEC_KEYS = frozenset({*RUBRIC_KEYS, "facts_file"})
PERSONA_KEYS = frozenset({"id", "description"})
DIMENSION_KEYS = frozenset({"id", "name", "question", "anchors", "weight", "personas"})
MEASURED_KEYS = frozenset({"id", "description", "max", "min"})
CHOICE_KEYS = frozenset({"choice", "user_said"})
# A one-line `facts` text that looks like a file name is refused: use `facts_file`.
# A facts text that starts with a home or relative path prefix is a path, not facts; one
# that starts with "/" is a path when it holds another slash ("/ marks a break" stays text).
HOME_OR_RELATIVE = ("~/", "./", "../")
FACTS_EXTENSIONS = ("md", "txt", "json", "yaml", "yml", "pdf", "csv", "html", "htm", "doc", "docx")


def dims_for(spec: dict[str, Any], persona_id: str) -> list[dict[str, Any]]:
    return [
        d
        for d in spec.get("dimensions") or []
        if not d.get("personas") or persona_id in d["personas"]
    ]


def _is_number(value: Any) -> bool:
    """A finite int or float; JSON's NaN and Infinity are refused."""
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _unknown(obj: dict[str, Any], allowed: frozenset[str], where: str) -> list[str]:
    return [f"{where}: unknown key {k!r}" for k in sorted(set(obj) - allowed)]


def _list_of_dicts(spec: dict[str, Any], key: str) -> list[dict[str, Any]] | None:
    value = spec.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        return None
    return value


def shape_errors(spec: Any) -> list[str]:
    """Types and keys. A spec with shape errors is not checked further."""
    if not isinstance(spec, dict):
        return ["the spec must be a JSON object"]
    errors = _unknown(spec, SPEC_KEYS, "spec")
    for key in ("title", "purpose", "facts", "facts_file"):
        if key in spec and not isinstance(spec[key], str):
            errors.append(f"spec.{key} must be a string")
    for key in ("personas", "dimensions", "measured", "declared_choices"):
        if _list_of_dicts(spec, key) is None:
            errors.append(f"spec.{key} must be a list of objects")
    if errors:
        return errors
    for p in spec.get("personas") or []:
        errors += _unknown(p, PERSONA_KEYS, f"persona {p.get('id')}")
        for key in ("id", "description"):
            if key in p and not isinstance(p[key], str):
                errors.append(f"persona {p.get('id')}: {key} must be a string")
    for d in spec.get("dimensions") or []:
        where = f"dimension {d.get('id')}"
        errors += _unknown(d, DIMENSION_KEYS, where)
        for key in ("id", "name", "question"):
            if key in d and not isinstance(d[key], str):
                errors.append(f"{where}: {key} must be a string")
        anchors = d.get("anchors", {})
        if not isinstance(anchors, dict):
            errors.append(f"{where}: anchors must be an object")
        else:
            errors += _unknown(anchors, ANCHOR_LEVELS, f"{where} anchors")
            if not all(isinstance(v, str) for v in anchors.values()):
                errors.append(f"{where}: every anchor must be a string")
        personas = d.get("personas", [])
        if not isinstance(personas, list) or not all(isinstance(x, str) for x in personas):
            errors.append(f"{where}: personas must be a list of persona ids")
    for m in spec.get("measured") or []:
        errors += _unknown(m, MEASURED_KEYS, f"measured {m.get('id')}")
        for key in ("id", "description"):
            if key in m and not isinstance(m[key], str):
                errors.append(f"measured {m.get('id')}: {key} must be a string")
    for c in spec.get("declared_choices") or []:
        errors += _unknown(c, CHOICE_KEYS, "declared choice")
        for key in ("choice", "user_said"):
            if key in c and not isinstance(c[key], str):
                errors.append(f"declared choice: {key} must be a string")
    ref = spec.get("template")
    if ref is not None:
        if not isinstance(ref, dict):
            errors.append("spec.template must be an object with id and version")
        else:
            errors += _unknown(ref, TEMPLATE_REF_KEYS, "spec.template")
            version = ref.get("version")
            if (
                not isinstance(ref.get("id"), str)
                or isinstance(version, bool)
                or not isinstance(version, int)
            ):
                errors.append("spec.template needs a string id and an integer version")
    framings = spec.get("framings")
    if framings is not None and (
        not isinstance(framings, list)
        or not framings
        or not all(isinstance(f, str) and f.strip() for f in framings)
    ):
        errors.append("spec.framings must be a list of non-empty strings")
    return errors


def dimensions_sha(dimensions: list[dict[str, Any]]) -> str:
    """A hash of what a template asks judges: each dimension's id, name, question, anchors."""
    core = [{k: d.get(k) for k in ("id", "name", "question", "anchors")} for d in dimensions or []]
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode("utf-8")).hexdigest()


def todo_paths(value: Any, where: str = "spec") -> list[str]:
    """Where a skeleton's TODO markers are still in place."""
    if isinstance(value, str):
        return [where] if TODO_MARKER in value else []
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in todo_paths(v, f"{where}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in todo_paths(v, f"{where}[{i}]")]
    return []


def validate_spec(spec: Any, cfg: Config | None = None) -> list[str]:
    cfg = resolve(cfg)
    errors = shape_errors(spec)
    if errors:
        return errors
    todo = todo_paths(spec)
    if todo:
        errors.append(f"fill the {TODO_MARKER} markers first: {', '.join(todo)}")
    for key in ("title", "purpose", "personas", "dimensions"):
        if not spec.get(key):
            errors.append(f"spec.{key} is missing or empty")
    personas = spec.get("personas") or []
    pids = [p.get("id") for p in personas]
    if len(set(pids)) != len(pids) or not all(pids):
        errors.append("every persona needs a unique id")
    for p in personas:
        if not p.get("description"):
            errors.append(f"persona {p.get('id')}: description missing")
    dids = []
    for d in spec.get("dimensions") or []:
        did = d.get("id")
        dids.append(did)
        anchors = d.get("anchors") or {}
        for lvl in REQUIRED_ANCHORS:
            if not str(anchors.get(lvl, "")).strip():
                errors.append(f"dimension {did}: anchor {lvl} missing (1/3/5 are required)")
        if not d.get("question"):
            errors.append(f"dimension {did}: question missing")
        w = d.get("weight", 1)
        if not _is_number(w) or w <= 0:
            errors.append(f"dimension {did}: weight must be a finite number > 0")
        for pid in d.get("personas") or []:
            if pid not in pids:
                errors.append(f"dimension {did}: unknown persona {pid}")
    if len(set(dids)) != len(dids) or not all(dids):
        errors.append("every dimension needs a unique id")
    for p in personas:
        if p.get("id") and not dims_for(spec, p["id"]):
            errors.append(f"persona {p['id']}: no dimension applies to it")
    run_defaults = cfg.run_defaults
    jp = spec.get("judges_per_persona", run_defaults["judges_per_persona"])
    if (
        isinstance(jp, bool)
        or not isinstance(jp, int)
        or not cfg.min_judges_per_persona <= jp <= MAX_JUDGES_PER_PERSONA
    ):
        errors.append(
            f"judges_per_persona must be an integer between {cfg.min_judges_per_persona} "
            f"and {MAX_JUDGES_PER_PERSONA}"
        )
    mr = spec.get("max_rounds", run_defaults["max_rounds"])
    if isinstance(mr, bool) or not isinstance(mr, int) or mr < cfg.min_max_rounds:
        errors.append(f"max_rounds must be an integer >= {cfg.min_max_rounds}")
    for key in RUN_BOUNDS:
        if key in spec:
            problem = run_value_error(key, spec[key])
            if problem:
                errors.append(problem)
    mids = [m.get("id") for m in spec.get("measured") or []]
    if len(set(mids)) != len(mids):
        errors.append("every measured criterion needs a unique id")
    for m in spec.get("measured") or []:
        if not m.get("id") or not m.get("description"):
            errors.append("every measured criterion needs an id and a description")
        for bound in ("max", "min"):
            if bound in m and not _is_number(m[bound]):
                errors.append(f"measured {m.get('id')}: {bound} must be a finite number")
        if _is_number(m.get("min")) and _is_number(m.get("max")) and m["min"] > m["max"]:
            errors.append(f"measured {m.get('id')}: min must not exceed max")
    for c in spec.get("declared_choices") or []:
        if not isinstance(c, dict) or not c.get("choice") or not c.get("user_said"):
            errors.append(
                "every declared choice needs `choice` and `user_said` (the user's own words): "
                "the author may not exempt a weakness by declaring it"
            )
    if "facts" in spec and "facts_file" in spec:
        errors.append("give either facts (text) or facts_file (a path), not both")
    return errors


def rubric_hash(spec: dict[str, Any]) -> str:
    core = {k: spec.get(k) for k in RUBRIC_KEYS if k not in OPTIONAL_HASH_KEYS or k in spec}
    canon = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def looks_like_path(text: str) -> bool:
    """A facts text that is a file name, not facts: one token with a slash, or one line
    ending in a known file extension."""
    stripped = text.strip()
    if len(stripped.split()) == 1 and "/" in stripped:
        return True
    if stripped.startswith(HOME_OR_RELATIVE):
        return True
    if stripped.startswith("/") and "/" in stripped[1:]:
        return True
    ext = r"\.(?:" + "|".join(FACTS_EXTENSIONS) + r")$"
    return len(stripped.splitlines()) == 1 and bool(re.search(ext, stripped, re.IGNORECASE))


def _freeze_facts(spec: dict[str, Any], base: Path, root: Path | None = None) -> None:
    facts_file = spec.pop("facts_file", None)
    if facts_file is not None:
        path = Path(facts_file).expanduser()
        if not path.is_absolute():
            path = base / path  # relative to the spec file's folder
        if root is not None and not path.resolve().is_relative_to(root):
            raise EvalError(f"facts_file {facts_file!r} is outside the root {root}")
        if not path.is_file():
            raise EvalError(f"facts_file does not exist: {facts_file!r}")
        spec["facts"] = read_text(path)  # frozen by content, not by path
    elif looks_like_path(spec.get("facts") or ""):
        raise EvalError(
            f"facts looks like a path, not facts: {spec['facts']!r}; use facts_file for a file"
        )


def _check_template_ref(spec: dict[str, Any], cfg: Config, lib: Path) -> None:
    """The template reference must name a template and version the library knows. Its hash is
    the template's own, and `modified` is computed here, never taken from the spec."""
    from . import library  # here, not at the top: library.py imports this module

    ref = spec["template"]
    served = library.all_templates(lib)[0].get(ref["id"])
    if served is None:
        raise EvalError(
            f"spec.template names no known template: {ref['id']!r} (library {lib}; "
            "pass --library to read another)"
        )
    known = [served["template"]]
    builtin = library.builtin_templates().get(ref["id"])
    if builtin is not None:
        known.append(builtin)
    match = next((t for t in known if t["version"] == ref["version"]), None)
    if match is None:
        versions = sorted({t["version"] for t in known})
        raise EvalError(
            f"spec.template {ref['id']!r} has no version {ref['version']} here (known: {versions})"
        )
    ref["sha256"] = dimensions_sha(match["dimensions"])
    # Frozen with the rubric: whether the dimensions still match the template they came from.
    ref["modified"] = dimensions_sha(spec["dimensions"]) != ref["sha256"]


def init_run(
    run: Path,
    spec_path: Path,
    cfg: Config | None = None,
    library: Path | None = None,
    facts_root: Path | None = None,
) -> str:
    """Validate the spec, freeze it and the configuration into `run`, return the rubric hash.
    With `facts_root`, a `facts_file` must resolve under it (the MCP server's root)."""
    cfg = resolve(cfg)
    if (run / "rubric.lock").exists():
        raise EvalError(f"{run} is already frozen; a new rubric needs a new run directory")
    if run.is_symlink() or (run.is_dir() and any(run.iterdir())):
        raise EvalError(
            f"{run} is not empty and is not a sealed-panel run: init needs a new or empty "
            "directory, so it never writes over another program's files"
        )
    spec = load_json(spec_path)
    errors = validate_spec(spec, cfg)
    if errors:
        raise EvalError("spec refused:\n  " + "\n  ".join(errors))
    for key, value in cfg.run_defaults.items():
        spec.setdefault(key, value)  # framings included, so the rubric hash covers them
    _freeze_facts(spec, Path(spec_path).parent, facts_root)
    lib = library_dir(library, cfg)  # resolved before anything is written: a refusal leaves no run
    if isinstance(spec.get("template"), dict):
        _check_template_ref(spec, cfg, lib)
    exposed = " ".join(
        [spec["title"], spec["purpose"]]
        + [p["description"] for p in spec["personas"]]
        + [c["choice"] for c in spec.get("declared_choices") or []]
    )
    if injection_flags(exposed, cfg):
        raise EvalError("the spec's own text addresses the judges: keep it neutral")
    run.mkdir(parents=True, exist_ok=True)
    frozen = json.loads(json.dumps(cfg.data))
    frozen["library"] = {**cfg.library, "path": str(lib)}
    cfg = Config(frozen)
    dump_json(run / "config.json", cfg.data)
    write_file(run / "config.lock", cfg.fingerprint + "\n")
    dump_json(run / "spec.json", spec)
    digest = rubric_hash(spec)
    write_file(run / "rubric.lock", digest + "\n")
    dump_json(run / "ledger.json", {"rubric_sha256": digest, "rounds": []})
    return digest


def load_frozen(run: Path) -> dict[str, Any]:
    lock = run / "rubric.lock"
    if not lock.exists():
        raise EvalError(f"{run} is not initialised (sealed-panel init)")
    spec: dict[str, Any] = load_json(run / "spec.json")
    if rubric_hash(spec) != read_text(lock).strip():
        raise EvalError(
            "the rubric changed after the freeze: scores are no longer comparable. "
            "Start a new run directory with the new spec."
        )
    return spec


def run_library(run: Path) -> Path:
    """The library frozen into a run at init; a run frozen by 0.1.0 resolves it now."""
    frozen = run_config(run).data.get("library") or {}
    return guard_library(Path(frozen["path"])) if frozen.get("path") else library_dir()


def library_for(flag: Path | None, run: Path | None) -> Path:
    """The library a rubrics command reads: the flag, else the one frozen into the run, else
    the user library (environment, setting, or user-level default)."""
    return library_dir(flag) if flag or not run else run_library(run)


def run_config(run: Path) -> Config:
    """The configuration frozen at init."""
    lock = run / "config.lock"
    if not lock.exists():
        raise EvalError(f"{run} is not initialised (sealed-panel init)")
    frozen = Config(load_json(run / "config.json"))
    if frozen.fingerprint != read_text(lock).strip():
        raise EvalError(
            "the configuration changed after the freeze: scores are no longer comparable. "
            "Start a new run directory."
        )
    return frozen
