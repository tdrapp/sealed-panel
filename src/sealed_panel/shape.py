"""Whether a value read from JSON has the shape a `TypedDict` declares: used to read a file of a
known format back (the result file), so a damaged one is refused instead of read as true."""

from __future__ import annotations

import types
import typing
from typing import Any

# The scalar types a TypedDict of this package names, by what JSON gives for each. A float
# field takes a JSON integer (a median of 4 is written 4); a bool is never an int here.
_SCALARS: dict[Any, tuple[type, ...]] = {
    str: (str,),
    bool: (bool,),
    int: (int,),
    float: (int, float),
    type(None): (type(None),),
}


def problem(value: object, tp: Any, where: str = "") -> str | None:
    """The first place where a value does not have the declared type, or None.

    Args:
        value: The value, as `json.load` returned it.
        tp: The declared type: a TypedDict, `list[X]`, `dict[str, X]`, `X | None` or a
            scalar.
        where: The value's place in the file, for the message.
    """
    if typing.is_typeddict(tp):
        return _record_problem(value, tp, where)
    if typing.get_origin(tp) is not None:
        return _generic_problem(value, tp, where)
    if isinstance(value, bool) and tp is not bool:
        return f"{where or 'the value'} is a boolean, not {tp.__name__}"
    if not isinstance(value, _SCALARS[tp]):
        return f"{where or 'the value'} is {type(value).__name__}, not {tp.__name__}"
    return None


def _generic_problem(value: object, tp: Any, where: str) -> str | None:
    """`X | None`, `list[X]` or `dict[str, X]`."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin in (types.UnionType, typing.Union):
        found = [problem(value, a, where) for a in args]
        return None if None in found else found[0]
    if origin is list:
        return _list_problem(value, args[0], where)
    return _map_problem(value, args[1], where)


def _record_problem(value: object, tp: Any, where: str) -> str | None:
    if not isinstance(value, dict):
        return f"{where or 'the file'} is {type(value).__name__}, not an object"
    hints = typing.get_type_hints(tp)
    missing = sorted(set(tp.__required_keys__) - set(value))
    unknown = sorted(set(value) - set(hints))
    if missing or unknown:
        return f"{where or 'the file'} lacks the keys {missing} or holds unknown keys {unknown}"
    for key, item in value.items():
        found = problem(item, hints[key], f"{where}.{key}" if where else str(key))
        if found:
            return found
    return None


def _list_problem(value: object, item: Any, where: str) -> str | None:
    if not isinstance(value, list):
        return f"{where} is {type(value).__name__}, not a list"
    return next((p for i, v in enumerate(value) if (p := problem(v, item, f"{where}[{i}]"))), None)


def _map_problem(value: object, item: Any, where: str) -> str | None:
    if not isinstance(value, dict):
        return f"{where} is {type(value).__name__}, not an object"
    return next((p for k, v in value.items() if (p := problem(v, item, f"{where}.{k}"))), None)
