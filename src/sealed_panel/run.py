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
from .model import FORMAT_SINCE, INJECTION_ACK, SCHEMA_VERSION, FrozenSpec, Spec
from .rubric import rubric_hash, validate_spec
from .settings import MISSING_KEY, UNKNOWN_KEY, Config, schema_errors
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
# The run format (SCHEMA_VERSION, see model.py for the rule a later release follows) is written
# to run.json last at init (a marker, not hashed): a run without it was created by a
# development version before the first release, or its init was interrupted.
RUN_FILE = "run.json"
# A release from 0.4.0 on is tagged, so a run of one of its formats can be finished with it.
_INSTALL_TAG = "pip install 'sealed-panel @ git+https://github.com/tdrapp/sealed-panel@v<version>'"
_FROM_ITS_TAG = (
    f"finish or read it with the version that created it, installed from its tag: {_INSTALL_TAG}"
)
# No version before the first release of the first format was published or tagged: a run it
# created can only be started again.
_FIRST_RELEASE = FORMAT_SINCE[min(FORMAT_SINCE)]
DEVELOPMENT_RUN = (
    f"a development version of sealed-panel before {_FIRST_RELEASE} created it, and such a run "
    f"cannot be continued: no version before {_FIRST_RELEASE} was published, and "
    f"{_FIRST_RELEASE} changed what a run freezes. Start a new run with this version"
)
NO_MARKER = (
    f"this run has no {RUN_FILE}: either {DEVELOPMENT_RUN}; or its init was interrupted before "
    f"writing {RUN_FILE}, which it writes last: start again in a new run directory"
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
    run: Path, spec_path: Path | Spec, cfg: Config, facts_root: Path | None, acknowledge: bool
) -> FrozenSpec:
    """The first half of init (see steps.init_run): refuse a run directory that is not new or
    empty, then read and validate the spec, refuse an acknowledgement this caller may not
    give, fill the per-run defaults and freeze the facts by content. Writes nothing.

    Args:
        run: A new or empty directory for the run.
        spec_path: The spec file, or a Spec.
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
    # Untrusted until validate_spec passes. A Spec has no facts_file to resolve.
    spec: Any
    if isinstance(spec_path, Spec):
        spec, base = spec_path.to_json(), Path.cwd()
    else:
        spec, base = load_json(spec_path), Path(spec_path).parent
    errors = validate_spec(spec, cfg)
    if errors:
        raise EvalError("spec refused:\n  " + "\n  ".join(errors))
    _check_acknowledgement(spec, acknowledge)
    for key, value in cfg.run_defaults.items():
        spec.setdefault(key, value)  # framings included, so the rubric hash covers them
    _freeze_facts(spec, base, facts_root)
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


def format_releases(version: int) -> str:
    """The releases that write a run format of the table, from FORMAT_SINCE: the first one, and
    the last one when a later format exists.

    Args:
        version: The run format, one FORMAT_SINCE holds or a later one.
    """
    since = FORMAT_SINCE[max(v for v in FORMAT_SINCE if v <= version)]
    later = [FORMAT_SINCE[v] for v in sorted(FORMAT_SINCE) if v > version]
    if later:
        return f"sealed-panel {since} up to the release before {later[0]}"
    return f"sealed-panel {since} or later"


def other_format(version: int) -> str:
    """Why a run of another format is refused, naming the releases of each format from
    FORMAT_SINCE; a format older than the table was made by a development version.

    Args:
        version: The run's format, not this version's.
    """
    if version > SCHEMA_VERSION:
        return (
            f"this run was created by a newer version of sealed-panel (run format {version}; "
            f"this version reads format {SCHEMA_VERSION}, written by "
            f"{format_releases(SCHEMA_VERSION)}): upgrade sealed-panel to a release that "
            f"writes format {version} to read or finish it"
        )
    if version < min(FORMAT_SINCE):
        return f"this run has run format {version}: {DEVELOPMENT_RUN}"
    return (
        f"this run was created by an earlier version of sealed-panel (run format {version}, "
        f"written by {format_releases(version)}); this version reads and writes format "
        f"{SCHEMA_VERSION} only, so it neither reads nor writes the run: {_FROM_ITS_TAG}"
    )


def refuse_older_run(run: Path) -> None:
    """Refuse a run of another format, for every command, read or write, before anything is
    created in it. A run without `run.json`, or of a format older than the table, was made by a
    development version before 0.4.0: it cannot be continued. A run of an earlier or newer
    release's format names the releases to use, from FORMAT_SINCE.

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
    if version != SCHEMA_VERSION:
        raise EvalError(other_format(version))


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


def frozen_config_refusal(shape: list[str]) -> str:
    """Why a run's frozen configuration is refused. Keys this release does not know, and only
    those: a newer release of the run's format froze it (upgrade). Keys it lacks, and only
    those: an earlier release of the format froze it, and this one has no value for them (finish
    it with that release). Anything else: the file is damaged.

    Args:
        shape: What schema_errors found.
    """
    unknown = [e for e in shape if e.startswith(UNKNOWN_KEY)]
    missing = [e for e in shape if e.startswith(MISSING_KEY)]
    if shape and unknown == shape:
        return (
            f"this run was frozen by a newer release of run format {SCHEMA_VERSION}: its "
            f"configuration holds settings this release does not know ({'; '.join(unknown)}). "
            "Upgrade sealed-panel to read or finish it"
        )
    if shape and missing == shape:
        return (
            f"this run was frozen by an earlier release of run format {SCHEMA_VERSION}, without "
            f"settings this release has no value for ({'; '.join(missing)}). A later release of "
            "the same format supplies any setting it added, so finish the run with the release "
            f"that created it ({format_releases(SCHEMA_VERSION)}, run format {SCHEMA_VERSION}), "
            f"installed from its tag: {_INSTALL_TAG}"
        )
    return f"the frozen configuration is damaged: {'; '.join(shape)}"


def run_config(run: Path) -> Config:
    """The configuration frozen at init."""
    refuse_older_run(run)
    lock = run / "config.lock"
    if not lock.exists():
        raise EvalError(f"{run} is not initialised (sealed-panel init)")
    data = load_json(run / "config.json")
    shape = schema_errors(data) if isinstance(data, dict) else ["not a table"]
    if shape:
        raise EvalError(frozen_config_refusal(shape))
    frozen = Config(data)
    if frozen.fingerprint != read_text(lock).strip():
        raise EvalError(
            "the configuration changed after the freeze: scores are no longer comparable. "
            "Start a new run directory."
        )
    return frozen
