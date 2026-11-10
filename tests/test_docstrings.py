"""The public API documents its arguments: every function a library user calls has an `Args:`
section naming each argument. ruff's D417 checks that a section names them all; this checks
that the section is there."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

import pytest

import sealed_panel


def public_callables() -> list[tuple[str, Callable[..., Any]]]:
    found: list[tuple[str, Callable[..., Any]]] = []
    for name in sealed_panel.__all__:
        obj = getattr(sealed_panel, name)
        if inspect.isfunction(obj):
            found.append((name, obj))
        elif inspect.isclass(obj) and "__init__" in vars(obj):
            found.append((f"{name}.__init__", obj.__init__))
    return found


def arguments(fn: Callable[..., Any]) -> list[str]:
    return [p for p in inspect.signature(fn).parameters if p not in ("self", "cls")]


def test_the_public_api_is_found() -> None:
    names = [n for n, _ in public_callables()]
    assert "judge_round" in names and "Config.__init__" in names and len(names) > 20


@pytest.mark.parametrize(("name", "fn"), public_callables(), ids=lambda x: str(x))
def test_each_public_function_documents_its_arguments(name: str, fn: Callable[..., Any]) -> None:
    args = arguments(fn)
    if not args:
        return
    doc = inspect.getdoc(fn) or ""
    assert "\nArgs:\n" in doc, f"{name} has no Args section"
    section = doc.split("\nArgs:\n", 1)[1]
    for arg in args:
        assert f"    {arg}:" in section, f"{name}: {arg} is not described"
