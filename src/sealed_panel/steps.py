"""The steps a command line, the MCP server and the library API call: each one composes
the domain modules, so none of them needs another's knowledge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import templates
from .errors import EvalError
from .rubric import dimensions_sha
from .run import freeze, prepare_spec
from .settings import Config, resolve


def _check_template_ref(spec: dict[str, Any], cfg: Config, lib: Path) -> None:
    """The template reference must name a template and version the library knows. Its hash is
    the template's own, and `modified` is computed here, never taken from the spec."""
    ref = spec["template"]
    served = templates.all_templates(lib)[0].get(ref["id"])
    if served is None:
        raise EvalError(
            f"spec.template names no known template: {ref['id']!r} (library {lib}; "
            "pass --library to read another)"
        )
    known = [served["template"]]
    builtin = templates.builtin_templates().get(ref["id"])
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
    acknowledge: bool = True,
) -> str:
    """Validate the spec, freeze it and the configuration into `run`, return the rubric hash.
    With `facts_root`, a `facts_file` must resolve under it (the MCP server's root).

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file.
        cfg: The configuration; the packaged defaults when None.
        library: The user rubric library, read for a template the spec names; the default
            one when None.
        facts_root: A folder a `facts_file` must resolve under, or None.
        acknowledge: Whether this caller may freeze a spec that acknowledges injection flags:
            a person at the command line may, the MCP server's caller (the author) may not.
    """
    cfg = resolve(cfg)
    spec = prepare_spec(run, spec_path, cfg, facts_root, acknowledge)
    if isinstance(spec.get("template"), dict):
        _check_template_ref(spec, cfg, templates.library_dir(library))
    return freeze(run, spec, cfg)
