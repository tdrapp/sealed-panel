"""Load defaults.toml, merge an optional override file, check bounds, expose typed values.

The defaults define the shape: an override may only set keys that exist there, with a value
of the same type. One table is open (new keys allowed): `text.punctuation`. Settings that
guard the protocol have hard bounds, so an override can tighten a guard but never switch it
off.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from fractions import Fraction
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from .errors import ConfigError

OPEN_TABLES = frozenset({("text", "punctuation")})
SEVERITY_NAMES = ("blocking", "material", "cosmetic", "none")

# Hard bounds. A value outside them is refused, in a configuration file or in a spec.
RUN_BOUNDS: dict[str, tuple[float, float]] = {
    "control_max": (1.0, 2.5),
    "good_min": (3.5, 5.0),
    "noise_floor_min": (0.25, 1.0),
}
MIN_VALID_JUDGES_FLOOR = 2
MAX_REPLACEMENTS_CEILING = 5
MIN_QUOTE_CHARS_FLOOR = 12
MIN_FRAGMENT_CHARS_FLOOR = 6
MAX_BAD_SHARE_CEILING = Fraction(1, 3)
SEVERITY_CEILINGS = {"blocking": 3, "material": 4}
MAX_JUDGES_PER_PERSONA = 9
# Transcript keys that may be empty: an empty handback tool means none is read.
OPTIONAL_TRANSCRIPT_KEYS = frozenset({"handback_tool"})
# Seconds a judge process may run, at least and at most.
JUDGE_TIMEOUT_BOUNDS = (1, 3600)
# Bytes a judge may write to stdout, at least and at most.
REPLY_BYTES_BOUNDS = (1024, 16 << 20)
# An environment variable name, as passed to a judge process.
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Characters a declared judge model id may hold: one printable line.
MODEL_ID_MAX_CHARS = 200


def _type_ok(default: Any, value: Any) -> bool:
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, (int, float))
    if isinstance(default, list):
        if not isinstance(value, list):
            return False
        return not default or all(_type_ok(default[0], v) for v in value)
    return isinstance(value, type(default))


def merge(
    base: Mapping[str, Any], override: Mapping[str, Any], path: tuple[str, ...] = ()
) -> dict[str, Any]:
    """Return base updated with override, refusing unknown keys and wrong types."""
    out = dict(base)
    for key, value in override.items():
        where = ".".join((*path, key))
        if key not in base:
            sample = next(iter(base.values()), None)
            if path in OPEN_TABLES and sample is not None and _type_ok(sample, value):
                out[key] = value
                continue
            if path in OPEN_TABLES:
                raise ConfigError(f"{where} must be of type {type(sample).__name__}")
            raise ConfigError(f"unknown configuration key {where}")
        current = base[key]
        if isinstance(current, dict):
            if not isinstance(value, dict):
                raise ConfigError(f"{where} must be a table")
            out[key] = merge(current, value, (*path, key))
        elif not _type_ok(current, value):
            raise ConfigError(f"{where} must be of type {type(current).__name__}")
        else:
            out[key] = value
    return out


def run_value_error(key: str, value: Any) -> str | None:
    """Why a per-run value is out of bounds, or None. Used for configuration and spec alike."""
    if key not in RUN_BOUNDS:
        return None
    low, high = RUN_BOUNDS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return f"{key} must be a number"
    if not low <= value <= high:
        return f"{key} must be between {low:g} and {high:g}, got {value:g}"
    return None


def _fraction(value: Any) -> Fraction | None:
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def bound_errors(data: Mapping[str, Any]) -> list[str]:
    """Every bound the configuration breaks."""
    errors: list[str] = []
    run, limits = data["run"], data["limits"]
    for key in RUN_BOUNDS:
        problem = run_value_error(key, run[key])
        if problem:
            errors.append(f"run.{problem}")
    framings = run["framings"]
    if not framings or not all(isinstance(f, str) and f.strip() for f in framings):
        errors.append("run.framings must hold at least one non-empty stance")
    if limits["min_valid_judges"] < MIN_VALID_JUDGES_FLOOR:
        errors.append(f"limits.min_valid_judges must be >= {MIN_VALID_JUDGES_FLOOR}")
    if limits["min_judges_per_persona"] < limits["min_valid_judges"]:
        errors.append("limits.min_judges_per_persona must be >= limits.min_valid_judges")
    if not 0 <= limits["max_replacements"] <= MAX_REPLACEMENTS_CEILING:
        errors.append(f"limits.max_replacements must be between 0 and {MAX_REPLACEMENTS_CEILING}")
    if limits["min_max_rounds"] < 1:
        errors.append("limits.min_max_rounds must be >= 1")
    if run["judges_per_persona"] < limits["min_judges_per_persona"]:
        errors.append("run.judges_per_persona must be >= limits.min_judges_per_persona")
    if run["judges_per_persona"] > MAX_JUDGES_PER_PERSONA:
        errors.append(f"run.judges_per_persona must be <= {MAX_JUDGES_PER_PERSONA}")
    if run["max_rounds"] < limits["min_max_rounds"]:
        errors.append("run.max_rounds must be >= limits.min_max_rounds")
    caps = data["severity"]["caps"]
    if tuple(caps) != SEVERITY_NAMES:
        errors.append(f"severity.caps must name exactly {', '.join(SEVERITY_NAMES)}, in order")
    else:
        values = [caps[name] for name in SEVERITY_NAMES]
        if not all(1 <= v <= 5 for v in values):
            errors.append("severity.caps values must be between 1 and 5")
        if values != sorted(values):
            errors.append("severity.caps must not decrease from blocking to none")
        for name, ceiling in SEVERITY_CEILINGS.items():
            if caps[name] > ceiling:
                errors.append(f"severity.caps.{name} must be <= {ceiling}")
    findings = data["severity"]["findings"]
    if set(findings) - set(caps) or "blocking" not in findings:
        errors.append("severity.findings must include blocking and name only severities in caps")
    quotes = data["quotes"]
    if quotes["min_chars"] < MIN_QUOTE_CHARS_FLOOR:
        errors.append(f"quotes.min_chars must be >= {MIN_QUOTE_CHARS_FLOOR}")
    if not MIN_FRAGMENT_CHARS_FLOOR <= quotes["min_fragment_chars"] <= quotes["min_chars"]:
        errors.append(
            f"quotes.min_fragment_chars must be between {MIN_FRAGMENT_CHARS_FLOOR} "
            "and quotes.min_chars"
        )
    share = _fraction(quotes["max_bad_share"])
    if share is None:
        errors.append("quotes.max_bad_share must be a fraction such as 1/3")
    elif not 0 <= share <= MAX_BAD_SHARE_CEILING:
        errors.append(f"quotes.max_bad_share must be between 0 and {MAX_BAD_SHARE_CEILING}")
    errors += _text_errors(data["text"])
    library = data.get("library") or {}
    if "control_min_candidate" in library and not 1 <= library["control_min_candidate"] <= 5:
        errors.append("library.control_min_candidate must be between 1 and 5")
    if "control_gap" in library and not 0 <= library["control_gap"] <= 4:
        errors.append("library.control_gap must be between 0 and 4")
    errors += _injection_errors(data["injection"])
    errors += _judge_errors(data.get("judge") or default_data()["judge"])
    root = str((data.get("mcp") or {}).get("root", ""))
    if root.strip() and not (root.startswith("~") or Path(root).is_absolute()):
        errors.append(f"mcp.root must be an absolute path or start with ~, got {root!r}")
    transcript = data["transcript"]
    for key, value in transcript.items():
        if isinstance(value, str) and not value and key not in OPTIONAL_TRANSCRIPT_KEYS:
            errors.append(f"transcript.{key} must not be empty")
    return errors


# The one value that is not a single character: the built-in entry for the ellipsis character.
# NFKC already turns it into "..." before the map runs, so the entry has no effect; it stays so
# that runs frozen with it remain valid. No other key may map to "...".
ELLIPSIS = "\u2026"


def _letter_or_digit(s: str) -> bool:
    """A letter or digit in `s` or in its case folding: U+0345 is neither, but folds to iota."""
    return any(c.isalnum() for c in s + s.casefold())


def _text_errors(text: Mapping[str, Any]) -> list[str]:
    """The quote check's normalisation. A key is one character, not a letter, digit or
    whitespace. A value is exactly one character: not whitespace, not a letter or digit (after
    case folding too) and not stripped; the only exception is the built-in ELLIPSIS entry. So a
    mapping can turn one punctuation character into another, never delete one, split a word or
    join two words. A run of mapped and literal dots that normalises to an ellipsis reads as
    one, which a judge could write as "..." anyway, so no reach is gained. No other check
    uses these settings."""
    errors = []
    if any(c.isalnum() or c.isspace() for c in text["strip_chars"]):
        errors.append("text.strip_chars may hold punctuation only, no letter, digit or space")
    allowed = default_data()["text"]["strip_chars"]
    added = sorted(set(text["strip_chars"]) - set(allowed))
    if added:
        errors.append(
            f"text.strip_chars may not add to the default {allowed!r}: stripping more would "
            f"loosen the quote check (added {''.join(added)!r})"
        )
    dropped = sorted(set(allowed) - set(text["strip_chars"]))
    if dropped:
        errors.append(
            f"text.strip_chars may not drop from the default {allowed!r}: quotes of "
            f"markdown-emphasised text would fail (dropped {''.join(dropped)!r})"
        )
    for key, value in text["punctuation"].items():
        if len(key) != 1 or _letter_or_digit(key) or key.isspace():
            errors.append(
                f"text.punctuation: {key!r} must be one character, not a letter, digit or space"
            )
        if not (key == ELLIPSIS and value == "..."):
            if len(value) != 1 or value.isspace():
                errors.append(
                    f"text.punctuation: the value for {key!r} must be one character, not "
                    f"whitespace (only {ELLIPSIS!r} keeps its built-in '...')"
                )
            if _letter_or_digit(value):
                errors.append(
                    f"text.punctuation: the value for {key!r} may not hold a letter or digit"
                )
        # The map runs before stripping. A stripped character as a key would escape stripping;
        # as a value, it would make another character vanish: either changes the stripped set
        # by another name.
        stripped = set(text["strip_chars"])
        if key in stripped:
            errors.append(
                f"text.punctuation: {key!r} is a stripped character and may not be mapped"
            )
        if stripped & set(value):
            errors.append(
                f"text.punctuation: the value for {key!r} may not hold a stripped character"
            )
    return errors


def _injection_errors(injection: Mapping[str, Any]) -> list[str]:
    """Patterns may be added, never dropped, and each must match its sample line once the
    sample goes through the scan's own normalisation, the built-in one."""
    from .text import fixed_normalise  # here, not at the top: text.py imports this module

    errors = []
    patterns, samples = injection["patterns"], injection["samples"]
    default = default_data()["injection"]["patterns"]
    missing = [p for p in default if p not in patterns]
    if missing:
        errors.append(
            "injection.patterns may be extended, but every default pattern must stay: "
            f"missing {missing}"
        )
    if len(samples) != len(patterns):
        errors.append("injection.samples needs one sample line per pattern, in the same order")
        return errors
    for p, sample in zip(patterns, samples, strict=True):
        try:
            hit = re.search(p, fixed_normalise(sample))
        except re.error as exc:
            errors.append(f"injection.patterns: {p!r} is not a valid expression ({exc})")
            continue
        if not hit:
            errors.append(f"injection.patterns: {p!r} does not match its sample {sample!r}")
    return errors


def _judge_errors(judge: Mapping[str, Any]) -> list[str]:
    """The judge runner: an argument list of non-empty strings (empty: host mode), a bounded
    timeout, and environment variable names."""
    errors = []
    command = judge["command"]
    if not all(isinstance(arg, str) and arg for arg in command):
        errors.append("judge.command must be a list of non-empty strings (empty: host mode)")
    low, high = JUDGE_TIMEOUT_BOUNDS
    if not low <= judge["timeout_s"] <= high:
        errors.append(f"judge.timeout_s must be between {low} and {high} seconds")
    low, high = REPLY_BYTES_BOUNDS
    if not low <= judge.get("max_reply_bytes", low) <= high:
        errors.append(f"judge.max_reply_bytes must be between {low} and {high}")
    if not low <= judge.get("max_stderr_bytes", low) <= high:
        errors.append(f"judge.max_stderr_bytes must be between {low} and {high}")
    names = judge["env_allow"]
    if not all(isinstance(n, str) and ENV_NAME.fullmatch(n) for n in names):
        errors.append("judge.env_allow must hold environment variable names only")
    model_id = judge.get("model_id", "")
    if not model_id.isprintable() or len(model_id) > MODEL_ID_MAX_CHARS:
        errors.append(
            f"judge.model_id must be one printable line of at most {MODEL_ID_MAX_CHARS} characters"
        )
    return errors


def default_data() -> dict[str, Any]:
    text = resources.files("sealed_panel").joinpath("defaults.toml").read_text("utf-8")
    return tomllib.loads(text)


class Config:
    """The effective configuration: defaults merged with an optional override."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self.data: dict[str, Any] = json.loads(json.dumps(data))
        errors = bound_errors(self.data)
        if errors:
            raise ConfigError("configuration refused:\n  " + "\n  ".join(errors))

    @property
    def fingerprint(self) -> str:
        canon = json.dumps(self.data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def section(self, name: str) -> dict[str, Any]:
        value: dict[str, Any] = self.data[name]
        return value

    @property
    def run_defaults(self) -> dict[str, Any]:
        return self.section("run")

    @property
    def framings(self) -> list[str]:
        return list(self.section("run")["framings"])

    @property
    def min_judges_per_persona(self) -> int:
        return int(self.section("limits")["min_judges_per_persona"])

    @property
    def min_max_rounds(self) -> int:
        return int(self.section("limits")["min_max_rounds"])

    @property
    def min_valid_judges(self) -> int:
        return int(self.section("limits")["min_valid_judges"])

    @property
    def max_replacements(self) -> int:
        return int(self.section("limits")["max_replacements"])

    @property
    def severity_caps(self) -> dict[str, int]:
        return {k: int(v) for k, v in self.section("severity")["caps"].items()}

    @property
    def severities(self) -> tuple[str, ...]:
        return tuple(self.severity_caps)

    @property
    def findings(self) -> tuple[str, ...]:
        return tuple(self.section("severity")["findings"])

    @property
    def min_quote_chars(self) -> int:
        return int(self.section("quotes")["min_chars"])

    @property
    def min_fragment_chars(self) -> int:
        return int(self.section("quotes")["min_fragment_chars"])

    @property
    def max_bad_share(self) -> Fraction:
        return Fraction(self.section("quotes")["max_bad_share"])

    @property
    def strip_chars(self) -> str:
        return str(self.section("text")["strip_chars"])

    @property
    def punctuation(self) -> dict[str, str]:
        return dict(self.section("text")["punctuation"])

    @property
    def injection_patterns(self) -> list[str]:
        return list(self.section("injection")["patterns"])

    @property
    def transcript(self) -> dict[str, Any]:
        return self.section("transcript")

    @property
    def judge(self) -> dict[str, Any]:
        """The [judge] section; a run frozen before 0.3.0 has none, and gets host mode."""
        value: dict[str, Any] = self.data.get("judge") or default_data()["judge"]
        return value

    @property
    def library(self) -> dict[str, Any]:
        """The [library] section; a run frozen by 0.1.0 has none, and gets the defaults."""
        value: dict[str, Any] = self.data.get("library") or default_data()["library"]
        return value


@cache
def default_config() -> Config:
    return Config(default_data())


def load_config(path: Path | None = None) -> Config:
    """The defaults, or the defaults merged with the TOML file at `path`."""
    if path is None:
        return default_config()
    try:
        override = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    return Config(merge(default_data(), override))


def resolve(cfg: Config | None) -> Config:
    return default_config() if cfg is None else cfg


LIBRARY_ENV = "SEALED_PANEL_RUBRICS"


def default_library_dir() -> Path:
    """The user-level rubric library: never relative to the current directory."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return base / "sealed-panel" / "rubrics"


def _git_top(path: Path) -> Path | None:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return parent
    return None


@cache
def _git_local_env_vars() -> frozenset[str]:
    """The repository-local variables, as git lists them: the ones git itself clears when it
    moves to another repository (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE and the like)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"],  # noqa: S607 (the user's own git, from PATH)
        capture_output=True,
        text=True,
        check=True,
        env=clean,
    )
    return frozenset(done.stdout.split())


def _git_ignored(top: Path, path: Path) -> bool:
    """Whether the user's git ignores `path` in the repository at `top`. An inherited
    repository-local variable (a git hook, a wrapper) would make git answer for another
    repository, so those are dropped; the user's configuration (GIT_CONFIG_GLOBAL and the like)
    is kept, so the answer is the one their own git gives. Git classes `-c` settings
    (GIT_CONFIG_PARAMETERS, GIT_CONFIG_COUNT) as local too, so a wrapper's one-off
    `-c core.excludesFile` is dropped with them: the user's durable configuration decides."""
    try:
        local = _git_local_env_vars()
        done = subprocess.run(  # noqa: S603 (a fixed argument list, no shell)
            # A trailing slash: the library is a directory, even before it exists.
            ["git", "-C", str(top), "check-ignore", "-q", "--no-index", str(path) + "/"],  # noqa: S607
            capture_output=True,
            check=False,
            env={k: v for k, v in os.environ.items() if k not in local},
        )
    except (OSError, subprocess.CalledProcessError):
        return False  # no usable git: the library cannot be shown to be ignored
    return done.returncode == 0


def guard_library(path: Path) -> Path:
    """Refuse a library inside the installed package, or inside a git working tree that does
    not ignore it: candidates quote the judged texts and must never be committed by accident.
    The path is resolved first (symlinks and `..` followed), so the checks see where candidates
    would really be written; the resolved path is returned."""
    real = path.expanduser().resolve(strict=False)
    package = Path(__file__).resolve().parent
    if real == package or package in real.parents:
        raise ConfigError(f"the rubric library may not live inside the package: {real}")
    run = next((p for p in (real, *real.parents) if (p / "rubric.lock").is_file()), None)
    if run is not None:
        raise ConfigError(
            f"the rubric library may not live inside the run {run}: its files would be read "
            "as templates and candidates"
        )
    top = _git_top(real)
    if top is not None and not _git_ignored(top, real):
        raise ConfigError(
            f"the rubric library {real} is inside the git working tree {top} and not ignored "
            "there: candidates quote the judged texts. Choose a path outside it, or ignore it."
        )
    return real


def library_dir(flag: str | Path | None = None, cfg: Config | None = None) -> Path:
    """The user library: the flag, else $SEALED_PANEL_RUBRICS, else the library.path setting,
    else the user-level default. `~` is expanded; a flag may be relative to the current
    directory, the environment variable and the setting may not. The result is absolute and
    checked by guard_library."""
    if flag is not None and str(flag).strip():
        return guard_library(Path(flag).expanduser().absolute())
    for source, value in (
        (f"${LIBRARY_ENV}", os.environ.get(LIBRARY_ENV, "")),
        ("library.path", str(resolve(cfg).library["path"])),
    ):
        if value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                raise ConfigError(
                    f"{source} must be an absolute path or start with ~, got {value!r}"
                )
            return guard_library(path)
    return guard_library(default_library_dir())


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, name + "."))
        else:
            flat[name] = value
    return flat


SHOW_LIMIT = 80
# Settings that place files and do not change a grade: never listed as overrides.
NOT_GRADING = frozenset({"library.path", "mcp.root"})


# Settings always shown in full: what runs as the judge is never reduced to a hash.
SHOW_IN_FULL = frozenset({"judge.command", "judge.env_allow"})


def _show(value: Any, where: str, key: str = "") -> str:
    """A value in full, or, when it is long, its size and short hash and where to read it."""
    text = json.dumps(value, ensure_ascii=True)
    if len(text) <= SHOW_LIMIT or key in SHOW_IN_FULL:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return f"<{len(text)} characters, sha256 {digest}, see {where}>"


_ABSENT = object()


def _differences(
    left: Mapping[str, Any], right: Mapping[str, Any], default: Mapping[str, Any], prefix: str
) -> list[str]:
    found = []
    for key in sorted(set(left) | set(right)):
        name = f"{prefix}{key}"
        if name in NOT_GRADING:
            continue
        here = default.get(key, _ABSENT) if isinstance(default, Mapping) else _ABSENT
        lv, rv = left.get(key, here), right.get(key, here)
        if isinstance(lv, Mapping) and isinstance(rv, Mapping):
            found += _differences(lv, rv, here if isinstance(here, Mapping) else {}, name + ".")
        elif lv != rv:
            found.append(name)
    return found


def config_differences(run: Config, server: Config) -> list[str]:
    """The settings of a run that differ from a server's, section by section (never through a
    flattened view a dotted key could shadow). A key the run lacks, frozen by an older version,
    counts as its default; the settings that only place files are left aside."""
    return _differences(run.data, server.data, default_data(), "")


def schema_errors(
    data: Mapping[str, Any], shape: Mapping[str, Any] | None = None, path: tuple[str, ...] = ()
) -> list[str]:
    """A frozen configuration holds only keys the defaults know, and no key with a dot: a dotted
    key would read as another setting in a flattened view. Keys may be missing (an older run);
    the open table (text.punctuation) takes any key, its bounds check the rest."""
    shape = default_data() if shape is None else shape
    errors: list[str] = []
    if path in OPEN_TABLES:
        return errors
    for key, value in data.items():
        where = ".".join((*path, key))
        if "." in key:
            errors.append(f"configuration key {where!r} holds a dot")
        elif key not in shape:
            errors.append(f"unknown configuration key {where!r}")
        elif isinstance(value, Mapping) and isinstance(shape[key], Mapping):
            errors += schema_errors(value, shape[key], (*path, key))
    return errors


def overrides(cfg: Config, spec: Mapping[str, Any] | None = None) -> list[str]:
    """Every setting that differs from the packaged defaults, as `key = value (default x)`.

    Per-run keys are read from the frozen spec when one is given, since that is the value
    the run uses; the other keys come from the configuration."""
    defaults = _flatten(default_data())
    current = _flatten(cfg.data)
    out = []
    for key in sorted(set(defaults) | set(current)):
        if key.startswith("run.") and spec is not None:
            continue
        if key in NOT_GRADING:
            continue  # where files are, not how a text is graded
        if key not in current:
            continue  # a key newer than the run's frozen configuration keeps its default
        if current.get(key) != defaults.get(key):
            now = _show(current.get(key), "config.json", key)
            was = _show(defaults.get(key), "defaults", key)
            out.append(f"{key} = {now} (default {was})")
    if spec is not None:
        for key, default in default_data()["run"].items():
            if key in spec and spec[key] != default:
                now, was = _show(spec[key], "spec.json"), _show(default, "defaults")
                out.append(f"spec.{key} = {now} (default {was})")
    return out
