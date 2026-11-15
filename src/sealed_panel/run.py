"""A run directory: freeze a spec and the configuration into it, and read them back
checked. Also the run's format marker (`run.json`) and its salt."""

from __future__ import annotations

import functools
import re
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import Any, Concatenate, ParamSpec, TypeVar

from .errors import EvalError
from .model import INJECTION_ACK, FrozenSpec
from .rubric import rubric_hash, validate_spec
from .settings import Config, schema_errors
from .store import (
    contained,
    dump_json,
    is_run,
    load_json,
    make_dir,
    outside_runs,
    read_text,
    write_file,
)
from .text import injection_flags

# A one-line `facts` text that looks like a file name is refused: use `facts_file`.
# A facts text that starts with a home or relative path prefix is a path, not facts; one
# that starts with "/" is a path when it holds another slash ("/ marks a break" stays text).
HOME_OR_RELATIVE = ("~/", "./", "../")
# The run's salt for the hashes of the judge environment's values, created at init.
ENV_SALT = "env.salt"
# The run format, written to run.json last at init (a marker, not hashed): a run without it
# was created before 0.4.0, or its init was interrupted.
SCHEMA_VERSION = 1
RUN_FILE = "run.json"
_FROM_ITS_TAG = (
    "finish or read it with the version that created it, installed from its tag: pip install "
    "'sealed-panel @ git+https://github.com/tdrapp/sealed-panel@v<version>'"
)
NO_MARKER = (
    f"this run has no {RUN_FILE}: either an earlier version of sealed-panel created it (0.4.0 "
    f"changed what a run freezes, so it neither reads nor writes such a run: {_FROM_ITS_TAG}), "
    f"or its init was interrupted before writing {RUN_FILE}, which it writes last (start again "
    "in a new run directory)"
)
OLDER_RUN = (
    "this run was created by an earlier version of sealed-panel: 0.4.0 changed what a run "
    f"freezes, so it neither reads nor writes it; {_FROM_ITS_TAG}"
)
SALT_BYTES = 32
FACTS_EXTENSIONS = ("md", "txt", "json", "yaml", "yml", "pdf", "csv", "html", "htm", "doc", "docx")


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
        if root is not None:
            path = contained(path, root, f"facts_file {facts_file!r}")
        if not path.is_file():
            raise EvalError(f"facts_file does not exist: {facts_file!r}")
        spec["facts"] = read_text(path)  # frozen by content, not by path
    elif looks_like_path(spec.get("facts") or ""):
        raise EvalError(
            f"facts looks like a path, not facts: {spec['facts']!r}; use facts_file for a file"
        )


def _check_acknowledgement(spec: dict[str, Any], acknowledge: bool) -> None:
    """Refuse a spec that acknowledges injection flags, unless a person froze it: whether a
    grade on a text addressed to a judge stands is not the author's call."""
    if spec.get(INJECTION_ACK) and not acknowledge:
        raise EvalError(
            f"the spec carries {INJECTION_ACK}: acknowledging text addressed to a judge is a "
            "person's decision, so the MCP tools never freeze it; run `sealed-panel init RUN "
            "--spec SPEC` at a terminal"
        )


def prepare_spec(
    run: Path, spec_path: Path, cfg: Config, facts_root: Path | None, acknowledge: bool
) -> FrozenSpec:
    """The first half of init (see steps.init_run): refuse a run directory that is not new or
    empty, then read and validate the spec, refuse an acknowledgement this caller may not
    give, fill the per-run defaults and freeze the facts by content. Writes nothing.

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file.
        cfg: The configuration.
        facts_root: A folder a `facts_file` must resolve under, or None.
        acknowledge: Whether this caller may freeze a spec that acknowledges injection flags.
    """
    if is_run(run):
        raise EvalError(f"{run} is already frozen; a new rubric needs a new run directory")
    outside_runs(run, "the run")
    if run.is_symlink() or (run.is_dir() and any(run.iterdir())):
        raise EvalError(
            f"{run} is not empty and is not a sealed-panel run: init needs a new or empty "
            "directory, so it never writes over another program's files"
        )
    spec = load_json(spec_path)  # untrusted until validate_spec passes
    errors = validate_spec(spec, cfg)
    if errors:
        raise EvalError("spec refused:\n  " + "\n  ".join(errors))
    _check_acknowledgement(spec, acknowledge)
    for key, value in cfg.run_defaults.items():
        spec.setdefault(key, value)  # framings included, so the rubric hash covers them
    _freeze_facts(spec, Path(spec_path).parent, facts_root)
    frozen: FrozenSpec = spec  # validated above, its defaults filled
    return frozen


def freeze(run: Path, spec: FrozenSpec, cfg: Config) -> str:
    """The second half of init: refuse a spec whose own text addresses the judges, then write
    the run's files, `run.json` last, and return the rubric hash.

    Args:
        run: A new or empty directory for the run.
        spec: The spec prepare_spec returned, its template reference checked.
        cfg: The configuration.
    """
    exposed = " ".join(
        [spec["title"], spec["purpose"]]
        + [p["description"] for p in spec["personas"]]
        + [c["choice"] for c in spec.get("declared_choices") or []]
    )
    if injection_flags(exposed):
        raise EvalError("the spec's own text addresses the judges: keep it neutral")
    make_dir(run)
    dump_json(run / "config.json", cfg.data)
    write_file(run / "config.lock", cfg.fingerprint + "\n")
    dump_json(run / "spec.json", spec)
    digest = rubric_hash(spec)
    write_file(run / "rubric.lock", digest + "\n")
    write_file(run / ENV_SALT, secrets.token_hex(SALT_BYTES) + "\n")
    # Written last: a run left without it by a crash is refused as incomplete, never read.
    dump_json(run / RUN_FILE, {"schema_version": SCHEMA_VERSION})
    return digest


def run_salt(run: Path) -> bytes | None:
    """The run's random salt for hashing the judge environment's values, or None when it
    cannot be read (a run file damaged by hand): then no hash is stored and no environment is
    compared; it never stops a judge.

    Args:
        run: The run directory.
    """
    try:
        salt = bytes.fromhex(read_text(run / ENV_SALT).strip())
    except (OSError, ValueError, EvalError):
        return None
    return salt if len(salt) == SALT_BYTES else None


def refuse_older_run(run: Path) -> None:
    """Refuse a run created before 0.4.0, for every command, read or write: such a run has no
    `run.json` holding this version's schema. 0.4.0 changed what a run freezes, so it neither
    reads nor writes an older one.

    Args:
        run: The run directory.
    """
    if not is_run(run):
        return  # not a run: the step's own check says so
    marker = run / RUN_FILE
    if not marker.exists() and not marker.is_symlink():
        raise EvalError(NO_MARKER)
    try:
        found = load_json(marker)
    except (EvalError, OSError):
        found = None
    version = found.get("schema_version") if isinstance(found, dict) else None
    if isinstance(version, bool) or not isinstance(version, int):
        raise EvalError(
            f'{marker} is damaged: it must hold {{"schema_version": {SCHEMA_VERSION}}}. A run\'s '
            "files are written by sealed-panel only: restore it from a copy, or start a new run"
        )
    if version > SCHEMA_VERSION:
        raise EvalError(
            f"this run was created by a newer version of sealed-panel (run format {version}; "
            f"this version reads format {SCHEMA_VERSION}): upgrade sealed-panel to read or "
            "finish it"
        )
    if version < SCHEMA_VERSION:
        raise EvalError(OLDER_RUN)


def load_frozen(run: Path) -> FrozenSpec:
    """The run's spec, refused when the run is older than 0.4.0 or changed after the freeze.

    Args:
        run: The run directory.
    """
    refuse_older_run(run)
    lock = run / "rubric.lock"
    if not is_run(run):
        raise EvalError(f"{run} is not initialised (sealed-panel init)")
    spec: FrozenSpec = load_json(run / "spec.json")
    if rubric_hash(spec) != read_text(lock).strip():
        raise EvalError(
            "the rubric changed after the freeze: scores are no longer comparable. "
            "Start a new run directory with the new spec."
        )
    return spec


P = ParamSpec("P")
R = TypeVar("R")


def writes_run(fn: Callable[Concatenate[Path, P], R]) -> Callable[Concatenate[Path, P], R]:
    """Run `fn(run, ...)` only for a run of this version (see refuse_older_run), checked before
    anything is created in it, the run's lock file included.

    Args:
        fn: A step whose first argument is the run directory.
    """

    @functools.wraps(fn)
    def checked(run: Path, /, *args: P.args, **kwargs: P.kwargs) -> R:
        refuse_older_run(run)
        return fn(run, *args, **kwargs)

    return checked


def run_config(run: Path) -> Config:
    """The configuration frozen at init."""
    refuse_older_run(run)
    lock = run / "config.lock"
    if not lock.exists():
        raise EvalError(f"{run} is not initialised (sealed-panel init)")
    data = load_json(run / "config.json")
    shape = schema_errors(data) if isinstance(data, dict) else ["not a table"]
    if shape:
        raise EvalError(f"the frozen configuration is damaged: {'; '.join(shape)}")
    frozen = Config(data)
    if frozen.fingerprint != read_text(lock).strip():
        raise EvalError(
            "the configuration changed after the freeze: scores are no longer comparable. "
            "Start a new run directory."
        )
    return frozen
