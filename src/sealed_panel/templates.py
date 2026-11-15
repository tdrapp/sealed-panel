"""Rubric templates: the built-in ones, the user's, suggestions and spec skeletons.

Built-in templates are package data in `sealed_panel/rubrics/`. The user library is a
user-level directory (see `library_dir`) that holds user templates (`*.json`, which override a
built-in of the same id). This module only reads the library: learned candidates and their
promotion live in `sealed_panel.learning`, the one place that writes a user template.
"""

from __future__ import annotations

import json
import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Any, NotRequired, TypedDict

from .errors import ConfigError, EvalError
from .model import Dimension, FrozenSpec, Measured, Persona
from .rubric import (
    DIMENSION_FIELDS,
    PERSONA_FIELDS,
    TODO_MARKER,
    dimensions_sha,
    keys_of,
    missing_anchors,
    todo_paths,
    validate_spec,
)
from .store import dump_json, load_json, outside_runs

LIBRARY_ENV = "SEALED_PANEL_RUBRICS"


def default_library_dir() -> Path:
    """The user-level rubric library: never relative to the current directory."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "sealed-panel" / "rubrics"


def guard_library(path: Path) -> Path:
    """Refuse a library inside the installed package or a run, for any use: its files would be
    read as templates and candidates. The path is resolved first (symlinks and `..` followed),
    so the checks see where candidates would really be written; the resolved path is
    returned."""
    real = path.expanduser().resolve(strict=False)
    package = Path(__file__).resolve().parent
    if real == package or package in real.parents:
        raise ConfigError(f"the rubric library may not live inside the package: {real}")
    outside_runs(real, "the rubric library")
    return real


def library_dir(flag: str | Path | None = None) -> Path:
    """The user library: the flag, else $SEALED_PANEL_RUBRICS, else the user-level default.
    `~` is expanded; a flag may be relative to the current directory, the environment variable
    may not. The result is absolute and checked by guard_library.

    Args:
        flag: The --library value, or None.
    """
    if flag is not None and str(flag).strip():
        return guard_library(Path(flag).expanduser().absolute())
    value = os.environ.get(LIBRARY_ENV, "")
    if value.strip():
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise ConfigError(
                f"${LIBRARY_ENV} must be an absolute path or start with ~, got {value!r}"
            )
        return guard_library(path)
    return guard_library(default_library_dir())


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
# A template's persona and dimension hold exactly the keys the shared table marks for one.
PERSONA_KEYS = keys_of(PERSONA_FIELDS, template=True)
DIMENSION_KEYS = keys_of(DIMENSION_FIELDS, template=True)
PROVENANCE_KEYS = frozenset({"date", "source", "change"})
MIN_DIMENSIONS, MAX_DIMENSIONS = 3, 6


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class Provenance(TypedDict):
    """One change to a template: when, from what, and what changed."""

    date: str
    source: str
    change: str


class BasedOn(TypedDict):
    """The built-in version a user template was made from."""

    version: int


class Template(TypedDict):
    """A rubric template, as `template_errors` accepts it."""

    id: str
    version: int
    title: str
    kinds: list[str]
    keywords: list[str]
    when_to_use: str
    personas: list[Persona]
    dimensions: list[Dimension]
    measured: list[Measured]
    provenance: list[Provenance]
    aliases: NotRequired[list[str]]
    facts_hint: NotRequired[str]
    based_on: NotRequired[BasedOn]


class Served(TypedDict):
    """A template the library serves: the template, where it comes from, and its file."""

    template: Template
    source: str
    path: Path | None


# -- templates ---------------------------------------------------------------------------


def _strs(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)


def _meta_errors(t: dict[str, Any]) -> list[str]:
    """The template's own keys: id, version, texts, kinds, keywords, aliases, base."""
    errors = []
    if not isinstance(t["id"], str) or not ID_PATTERN.match(t["id"]):
        errors.append("id must be lowercase letters, digits and dashes")
    if isinstance(t["version"], bool) or not isinstance(t["version"], int) or t["version"] < 1:
        errors.append("version must be an integer >= 1")
    for key in ("title", "when_to_use"):
        if not isinstance(t[key], str) or not t[key].strip():
            errors.append(f"{key} must be a non-empty string")
    return errors + _list_errors(t) + _optional_errors(t)


def _list_errors(t: dict[str, Any]) -> list[str]:
    errors = []
    if not _strs(t["kinds"]) or not t["kinds"]:
        errors.append("kinds must be a non-empty list of strings")
    if not _strs(t["keywords"]):
        errors.append("keywords must be a list of strings")
    if "aliases" in t and not _strs(t["aliases"]):
        errors.append("aliases must be a list of strings")
    return errors


def _optional_errors(t: dict[str, Any]) -> list[str]:
    errors = []
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
    return errors


def _persona_errors(personas: Any) -> list[str]:
    """A template's personas: a non-empty list, each with exactly the shared table's keys."""
    if not isinstance(personas, list) or not personas:
        return ["personas must be a non-empty list"]
    return [
        "each persona needs exactly an id and a description"
        for p in personas
        if not isinstance(p, dict) or set(p) != PERSONA_KEYS
    ]


def _dimension_errors(dims: Any) -> list[str]:
    """A template's dimensions: 3 to 6 (a template's own rule), each with exactly the shared
    table's template keys and the required anchors, ids unique."""
    if not isinstance(dims, list) or not MIN_DIMENSIONS <= len(dims) <= MAX_DIMENSIONS:
        return [f"dimensions must be a list of {MIN_DIMENSIONS} to {MAX_DIMENSIONS}"]
    errors, ids = [], []
    for d in dims:
        if not isinstance(d, dict) or set(d) != DIMENSION_KEYS:
            errors.append("each dimension needs exactly id, name, question and anchors")
            continue
        if not isinstance(d["id"], str):  # a list or an object would also break the set below
            errors.append(f"dimension {d['id']!r}: id must be a string")
            continue
        ids.append(d["id"])
        if missing_anchors(d["anchors"]):
            errors.append(f"dimension {d['id']}: anchors 1, 3 and 5 must be non-empty")
    if len(set(ids)) != len(ids):
        errors.append("dimension ids must be unique")
    return errors


def _provenance_errors(prov: Any) -> list[str]:
    if not isinstance(prov, list) or not prov:
        return ["provenance must be a non-empty list"]
    return [
        "each provenance entry needs exactly date, source and change"
        for entry in prov
        if not isinstance(entry, dict) or set(entry) != PROVENANCE_KEYS
    ]


def template_errors(t: Any) -> list[str]:
    """Every way a template breaks the format. An empty list means it is valid."""
    if not isinstance(t, dict):
        return ["a template must be a JSON object"]
    errors = [f"unknown key {k!r}" for k in sorted(set(t) - TEMPLATE_KEYS - OPTIONAL_TEMPLATE_KEYS)]
    errors += [f"missing key {k!r}" for k in sorted(TEMPLATE_KEYS - set(t))]
    if errors:
        return errors
    errors = _meta_errors(t) + _persona_errors(t["personas"]) + _dimension_errors(t["dimensions"])
    if not isinstance(t["measured"], list):
        errors.append("measured must be a list")
    errors += _provenance_errors(t["provenance"])
    if not errors and todo_paths(t, "template"):
        errors.append(f"a template must not hold {TODO_MARKER} markers")
    return errors


def checked_template(t: Any, where: str) -> Template:
    """The template, refused with every error template_errors finds, named by `where`."""
    errors = template_errors(t)
    if errors:
        raise EvalError(f"{where} is not a valid template:\n  " + "\n  ".join(errors))
    out: Template = t
    return out


def builtin_templates() -> dict[str, Template]:
    """The packaged rubric templates, by id."""
    folder = resources.files("sealed_panel").joinpath("rubrics")
    found = {}
    for entry in sorted(folder.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".json"):
            t = checked_template(
                json.loads(entry.read_text(encoding="utf-8")), f"built-in {entry.name}"
            )
            found[t["id"]] = t
    return found


def user_templates(lib: Path) -> tuple[dict[str, tuple[Template, Path]], list[str]]:
    """Valid user templates by id with their file, and a warning for each file skipped."""
    found: dict[str, tuple[Template, Path]] = {}
    warnings = []
    if lib.is_dir():
        for path in sorted(lib.glob("*.json")):
            if path.is_symlink():
                warnings.append(f"skipped {path}: a symbolic link")
                continue
            try:
                t = checked_template(json.loads(path.read_text(encoding="utf-8")), str(path))
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
            based = t.get("based_on")
            base = based["version"] if based else None
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


def check_template_ref(spec: FrozenSpec, lib: Path) -> None:
    """The template reference must name a template and version the library knows. Its hash is
    the template's own, and `modified` is computed here, never taken from the spec.

    Args:
        spec: The spec being frozen; its `template` reference is completed in place.
        lib: The user rubric library.
    """
    ref = spec["template"]
    served = all_templates(lib)[0].get(ref["id"])
    if served is None:
        raise EvalError(
            f"spec.template names no known template: {ref['id']!r} (library {lib}; "
            "pass --library to read another)"
        )
    known = [served["template"]]
    builtin = builtin_templates().get(ref["id"])
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


# -- suggest -----------------------------------------------------------------------------


def normalise_kind(kind: str) -> str:
    """A kind of text in the form templates use: folded case, dashes for spaces.

    Args:
        kind: The kind as the user wrote it.
    """
    return re.sub(r"[\s_]+", "-", kind.strip().casefold())


def _keyword_hits(t: Template, text: str) -> list[str]:
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
    rows: list[dict[str, Any]] = []
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


def instantiate(t: Template, personas: list[dict[str, Any]] | None = None) -> dict[str, Any]:
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
        row: dict[str, object] = dict(m)
        for bound in ("max", "min"):
            if bound in row:
                row[bound] = _todo(f"your {bound} (suggested {row[bound]})")
        measured.append(row)
    dims: list[Dimension] = [
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


def _skeleton_target(out: Path) -> None:
    """The one owner of where a skeleton goes, for the command line and MCP alike: never a file
    a run could be told by (`rubric.lock`), never a new folder, never inside a run, never over
    a file."""
    if out.suffix != ".json":
        raise EvalError(f"{out}: a spec is a .json file; name one")
    if not out.parent.is_dir():
        raise EvalError(f"{out.parent} is not a folder: the spec goes into an existing one")
    outside_runs(out, "the spec")
    if out.exists():
        raise EvalError(f"{out} already exists: choose another path or remove it first")


def write_skeleton(lib: Path, tid: str, out: Path, persona_file: Path | None = None) -> Path:
    """Write a spec skeleton from a template; its TODO markers stop `init` until filled.

    Args:
        lib: The user rubric library.
        tid: The template id.
        out: Where to write the spec: a new `.json` file in an existing folder outside any run.
        persona_file: A JSON list of personas to put in the skeleton, or None.
    """
    _skeleton_target(out)
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
