"""The rubric: validate a spec's shape and values, and hash what it asks."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .model import INJECTION_ACK, Dimension, FrozenSpec
from .settings import MAX_JUDGES_PER_PERSONA, RUN_BOUNDS, Config, resolve, run_value_error

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
    INJECTION_ACK,
)
# Keys hashed only when present, so a spec without them keeps the hash it had before.
OPTIONAL_HASH_KEYS = frozenset({"template", INJECTION_ACK})
# A string a skeleton leaves for the user to fill; `init` refuses a spec that still holds one.
TODO_MARKER = "TODO(sealed-panel)"
TEMPLATE_REF_KEYS = frozenset({"id", "version", "sha256", "modified"})
SPEC_KEYS = frozenset({*RUBRIC_KEYS, "facts_file"})


# -- the field table a spec and a template share ------------------------------------------

# A shape check: the errors for one key's value, named by where the value is.
Check = Callable[[str, Any, str], list[str]]


def _string(key: str, value: Any, where: str) -> list[str]:
    return [] if isinstance(value, str) else [f"{where}: {key} must be a string"]


def _persona_ids(key: str, value: Any, where: str) -> list[str]:
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return []
    return [f"{where}: {key} must be a list of persona ids"]


def _anchors(key: str, value: Any, where: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{where}: {key} must be an object"]
    errors = _unknown(value, ANCHOR_LEVELS, f"{where} {key}")
    if not all(isinstance(v, str) for v in value.values()):
        errors.append(f"{where}: every anchor must be a string")
    return errors


@dataclass(frozen=True)
class Field:
    """One key of a persona, a dimension, a measured criterion or a declared choice.

    `check` is the shape a present value must have in a spec (None: no shape check; a value
    rule may still apply). `template` marks the keys a template's persona or dimension holds,
    exactly: a template has its own stricter key rule and no type check of its own."""

    key: str
    check: Check | None = None
    template: bool = False


PERSONA_FIELDS = (Field("id", _string, True), Field("description", _string, True))
DIMENSION_FIELDS = (
    Field("id", _string, True),
    Field("name", _string, True),
    Field("question", _string, True),
    Field("anchors", _anchors, True),
    Field("weight"),
    Field("personas", _persona_ids),
)
MEASURED_FIELDS = (Field("id", _string), Field("description", _string), Field("max"), Field("min"))
CHOICE_FIELDS = (Field("choice", _string), Field("user_said", _string))


def keys_of(fields: tuple[Field, ...], template: bool = False) -> frozenset[str]:
    """The keys a table allows; with `template`, the exact keys a template holds.

    Args:
        fields: The table.
        template: Only the keys marked for a template.
    """
    return frozenset(f.key for f in fields if f.template or not template)


PERSONA_KEYS = keys_of(PERSONA_FIELDS)
DIMENSION_KEYS = keys_of(DIMENSION_FIELDS)
MEASURED_KEYS = keys_of(MEASURED_FIELDS)
CHOICE_KEYS = keys_of(CHOICE_FIELDS)


def missing_anchors(anchors: Any) -> list[str]:
    """The required anchor levels (1, 3 and 5) without a non-blank string, for a spec and a
    template alike.

    Args:
        anchors: A dimension's anchors, by level.
    """
    if not isinstance(anchors, dict):
        return list(REQUIRED_ANCHORS)
    return [
        lvl
        for lvl in REQUIRED_ANCHORS
        if not (isinstance(anchors.get(lvl), str) and anchors[lvl].strip())
    ]


def dims_for(spec: FrozenSpec, persona_id: str) -> list[Dimension]:
    """The dimensions a persona is scored on.

    Args:
        spec: The spec.
        persona_id: The persona's id.
    """
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


def _fields(
    obj: dict[str, Any], fields: tuple[Field, ...], allowed: frozenset[str], where: str
) -> list[str]:
    """Unknown keys, then each present key's shape, in the table's order."""
    errors = _unknown(obj, allowed, where)
    for f in fields:
        if f.check is not None and f.key in obj:
            errors += f.check(f.key, obj[f.key], where)
    return errors


def _template_ref_errors(ref: Any) -> list[str]:
    if ref is None:
        return []
    if not isinstance(ref, dict):
        return ["spec.template must be an object with id and version"]
    errors = _unknown(ref, TEMPLATE_REF_KEYS, "spec.template")
    version = ref.get("version")
    if (
        not isinstance(ref.get("id"), str)
        or isinstance(version, bool)
        or not isinstance(version, int)
    ):
        errors.append("spec.template needs a string id and an integer version")
    return errors


def _top_level_errors(spec: dict[str, Any]) -> list[str]:
    errors = _unknown(spec, SPEC_KEYS, "spec")
    for key in ("title", "purpose", "facts", "facts_file", INJECTION_ACK):
        if key in spec and not isinstance(spec[key], str):
            errors.append(f"spec.{key} must be a string")
    for key in ("personas", "dimensions", "measured", "declared_choices"):
        if _list_of_dicts(spec, key) is None:
            errors.append(f"spec.{key} must be a list of objects")
    return errors


def shape_errors(spec: Any) -> list[str]:
    """Types and keys. A spec with shape errors is not checked further."""
    if not isinstance(spec, dict):
        return ["the spec must be a JSON object"]
    errors = _top_level_errors(spec)
    if errors:
        return errors
    for p in spec.get("personas") or []:
        errors += _fields(p, PERSONA_FIELDS, PERSONA_KEYS, f"persona {p.get('id')}")
    for d in spec.get("dimensions") or []:
        errors += _fields(d, DIMENSION_FIELDS, DIMENSION_KEYS, f"dimension {d.get('id')}")
    for m in spec.get("measured") or []:
        errors += _fields(m, MEASURED_FIELDS, MEASURED_KEYS, f"measured {m.get('id')}")
    for c in spec.get("declared_choices") or []:
        errors += _fields(c, CHOICE_FIELDS, CHOICE_KEYS, "declared choice")
    errors += _template_ref_errors(spec.get("template"))
    framings = spec.get("framings")
    if framings is not None and (
        not isinstance(framings, list)
        or not framings
        or not all(isinstance(f, str) and f.strip() for f in framings)
    ):
        errors.append("spec.framings must be a list of non-empty strings")
    return errors


def dimensions_sha(dimensions: list[Dimension]) -> str:
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


def _persona_errors(spec: dict[str, Any]) -> list[str]:
    personas = spec.get("personas") or []
    pids = [p.get("id") for p in personas]
    errors = []
    if len(set(pids)) != len(pids) or not all(pids):
        errors.append("every persona needs a unique id")
    for p in personas:
        if not p.get("description"):
            errors.append(f"persona {p.get('id')}: description missing")
    return errors


def _one_dimension_errors(d: dict[str, Any], pids: list[Any]) -> list[str]:
    did = d.get("id")
    errors = [
        f"dimension {did}: anchor {lvl} missing (1/3/5 are required)"
        for lvl in missing_anchors(d.get("anchors") or {})
    ]
    if not d.get("question"):
        errors.append(f"dimension {did}: question missing")
    w = d.get("weight", 1)
    if not _is_number(w) or w <= 0:
        errors.append(f"dimension {did}: weight must be a finite number > 0")
    for pid in d.get("personas") or []:
        if pid not in pids:
            errors.append(f"dimension {did}: unknown persona {pid}")
    return errors


def _dimension_errors(spec: Any) -> list[str]:
    """The dimensions' values, on a spec whose shape passed."""
    pids = [p.get("id") for p in spec.get("personas") or []]
    dims = spec.get("dimensions") or []
    errors = [e for d in dims for e in _one_dimension_errors(d, pids)]
    dids = [d.get("id") for d in dims]
    if len(set(dids)) != len(dids) or not all(dids):
        errors.append("every dimension needs a unique id")
    return errors


def _unscored_personas(spec: Any) -> list[str]:
    """A persona no dimension applies to, on a spec whose shape passed."""
    pids = [p.get("id") for p in spec.get("personas") or []]
    return [
        f"persona {pid}: no dimension applies to it"
        for pid in pids
        if pid and not dims_for(spec, pid)
    ]


def _run_errors(spec: dict[str, Any], cfg: Config) -> list[str]:
    errors = []
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
    return errors


def _measured_errors(spec: dict[str, Any]) -> list[str]:
    errors = []
    mids = [m.get("id") for m in spec.get("measured") or []]
    if len(set(mids)) != len(mids):
        errors.append("every measured criterion needs a unique id")
    for m in spec.get("measured") or []:
        errors += _one_measured_errors(m)
    return errors


def _one_measured_errors(m: dict[str, Any]) -> list[str]:
    errors = []
    if not m.get("id") or not m.get("description"):
        errors.append("every measured criterion needs an id and a description")
    for bound in ("max", "min"):
        if bound in m and not _is_number(m[bound]):
            errors.append(f"measured {m.get('id')}: {bound} must be a finite number")
    if _is_number(m.get("min")) and _is_number(m.get("max")) and m["min"] > m["max"]:
        errors.append(f"measured {m.get('id')}: min must not exceed max")
    return errors


def _text_errors(spec: dict[str, Any]) -> list[str]:
    errors = []
    for c in spec.get("declared_choices") or []:
        if not isinstance(c, dict) or not c.get("choice") or not c.get("user_said"):
            errors.append(
                "every declared choice needs `choice` and `user_said` (the user's own words): "
                "the author may not exempt a weakness by declaring it"
            )
    if "facts" in spec and "facts_file" in spec:
        errors.append("give either facts (text) or facts_file (a path), not both")
    if INJECTION_ACK in spec and not spec[INJECTION_ACK].strip():
        errors.append(
            f"spec.{INJECTION_ACK} must say why a text addressed to a judge belongs in the "
            "candidate; leave the key out otherwise"
        )
    return errors


def validate_spec(spec: Any, cfg: Config | None = None) -> list[str]:
    """Every problem with a spec, in shape and in values; empty when it can be frozen.

    Args:
        spec: The spec as loaded from JSON.
        cfg: The configuration; the packaged defaults when None.
    """
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
    return (
        errors
        + _persona_errors(spec)
        + _dimension_errors(spec)
        + _unscored_personas(spec)
        + _run_errors(spec, cfg)
        + _measured_errors(spec)
        + _text_errors(spec)
    )


def rubric_hash(spec: FrozenSpec) -> str:
    """The sha256 of what the rubric asks: the hashed keys of the spec.

    Args:
        spec: The spec.
    """
    core = {k: spec.get(k) for k in RUBRIC_KEYS if k not in OPTIONAL_HASH_KEYS or k in spec}
    canon = json.dumps(core, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
