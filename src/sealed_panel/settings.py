"""Load defaults.toml, merge an optional override file, check bounds, expose typed values.

The defaults define the shape: an override may only set keys that exist there, with a value
of the same type. Settings that guard the protocol have hard bounds, so an override can
tighten a guard but never switch it off. A section 0.4.0 fixed in code, and a transcript field
name 0.4.1 replaced with host adapters, is refused, by name.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from fractions import Fraction
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, TypedDict

from .errors import ConfigError

# Sections a version before 0.4.0 froze into every run, and 0.4.0 fixed in code: a file that
# sets one is refused, naming the key.
REMOVED_SECTIONS = {
    "text": "the normalisation of texts",
    "injection": "the injection patterns",
    "library": (
        "learning runs only through `sealed-panel learning learn`, and the library comes from "
        "--library or $SEALED_PANEL_RUBRICS"
    ),
}
# The field names of a transcript entry, settings of [transcript] until 0.4.1 replaced them with
# host adapters, with the value 0.4.0 froze by default: a file that sets one is refused, naming
# it, and a run frozen with exactly these values reads as `format = "agent-jsonl"` (run.py).
REMOVED_TRANSCRIPT_KEYS: dict[str, str] = {
    "role_key": "type",
    "assistant_role": "assistant",
    "user_role": "user",
    "message_key": "message",
    "content_key": "content",
    "block_type_key": "type",
    "text_block_type": "text",
    "text_key": "text",
    "agent_id_key": "agentId",
    "handback_tool": "SubagentHandback",
    "tool_block_type": "tool_use",
    "tool_name_key": "name",
    "tool_input_key": "input",
    "tool_message_key": "message",
    "tool_id_key": "id",
    "tool_result_type": "tool_result",
    "tool_result_id_key": "tool_use_id",
    "tool_error_key": "is_error",
}
# The transcript formats a host adapter reads: the keys of sealed_panel.hosts.FORMATS, which a
# test holds equal. The first one reads what the field names above described.
TRANSCRIPT_FORMATS = ("agent-jsonl", "rollout-jsonl")
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
# Seconds a judge process may run, at least and at most.
JUDGE_TIMEOUT_BOUNDS = (1, 3600)
# Bytes a judge may write to stdout, at least and at most.
REPLY_BYTES_BOUNDS = (1024, 16 << 20)
# An environment variable name, as passed to a judge process.
ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Characters a declared judge model id may hold: one printable line.
MODEL_ID_MAX_CHARS = 200


class JudgeSettings(TypedDict):
    """The [judge] section: the judge command (empty in host mode), its bounds, the environment
    names it may get, and the model id the run declares."""

    command: list[str]
    timeout_s: int
    max_reply_bytes: int
    max_stderr_bytes: int
    env_allow: list[str]
    model_id: str


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
        if not path and key in REMOVED_SECTIONS:
            raise ConfigError(removed_message(key, value))
        if path == ("transcript",) and key in REMOVED_TRANSCRIPT_KEYS:
            raise ConfigError(removed_transcript_message(override))
        if key not in base:
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


def removed_message(section: str, value: Any) -> str:
    """The refusal of a section 0.4.0 removed, naming the keys the file sets in it.

    Args:
        section: The section's name.
        value: What the file sets there.
    """
    keys = [f"{section}.{k}" for k in value] if isinstance(value, dict) and value else [section]
    return (
        f"{', '.join(keys)}: no longer a setting, fixed in code since 0.4.0 "
        f"({REMOVED_SECTIONS[section]}); remove [{section}] from the configuration file"
    )


def removed_transcript_message(section: Mapping[str, Any]) -> str:
    """The refusal of the transcript field names 0.4.1 removed, naming each one the file sets.

    Args:
        section: The [transcript] table of the file.
    """
    keys = ", ".join(f"transcript.{k}" for k in section if k in REMOVED_TRANSCRIPT_KEYS)
    return (
        f"{keys}: no longer a setting since 0.4.1: a host adapter reads each transcript format "
        "by its own structure, and transcript.format names the format; remove the field names "
        "from the configuration file"
    )


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
    errors += _judge_errors(data["judge"])
    root = str((data.get("mcp") or {}).get("root", ""))
    if root.strip() and not (root.startswith("~") or Path(root).is_absolute()):
        errors.append(f"mcp.root must be an absolute path or start with ~, got {root!r}")
    if data["transcript"]["format"] not in TRANSCRIPT_FORMATS:
        errors.append(f"transcript.format must be one of: {', '.join(TRANSCRIPT_FORMATS)}")
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
    if not low <= judge["max_reply_bytes"] <= high:
        errors.append(f"judge.max_reply_bytes must be between {low} and {high}")
    if not low <= judge["max_stderr_bytes"] <= high:
        errors.append(f"judge.max_stderr_bytes must be between {low} and {high}")
    names = judge["env_allow"]
    if not all(isinstance(n, str) and ENV_NAME.fullmatch(n) for n in names):
        errors.append("judge.env_allow must hold environment variable names only")
    model_id = judge["model_id"]
    if not model_id.isprintable() or len(model_id) > MODEL_ID_MAX_CHARS:
        errors.append(
            f"judge.model_id must be one printable line of at most {MODEL_ID_MAX_CHARS} characters"
        )
    return errors


@cache
def _parsed_defaults() -> dict[str, Any]:
    text = resources.files("sealed_panel").joinpath("defaults.toml").read_text("utf-8")
    return tomllib.loads(text)


def default_data() -> dict[str, Any]:
    """The packaged defaults, read from defaults.toml once per process; each call returns its
    own copy, which the caller may change."""
    return copy.deepcopy(_parsed_defaults())


def fingerprint(data: Any) -> str:
    """The sha256 of settings as frozen: the canonical JSON of the data, whatever its shape.

    Args:
        data: The settings, as read from a run's config.json.
    """
    canon = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Config:
    """The effective configuration: defaults merged with an optional override."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        """Check the settings against their bounds and keep a copy.

        Args:
            data: The settings, shaped like the defaults.
        """
        self.data: dict[str, Any] = json.loads(json.dumps(data))
        errors = bound_errors(self.data)
        if errors:
            raise ConfigError("configuration refused:\n  " + "\n  ".join(errors))

    @property
    def fingerprint(self) -> str:
        """The sha256 of the settings, frozen into a run."""
        return fingerprint(self.data)

    def section(self, name: str) -> dict[str, Any]:
        """One table of the settings.

        Args:
            name: The table's name, such as `run` or `judge`.
        """
        value: dict[str, Any] = self.data[name]
        return value

    @property
    def run_defaults(self) -> dict[str, Any]:
        """The per-run defaults a spec may override."""
        return self.section("run")

    @property
    def framings(self) -> list[str]:
        """The reading stances rotated across judges."""
        return list(self.section("run")["framings"])

    @property
    def min_judges_per_persona(self) -> int:
        """The lowest judges_per_persona a spec may ask."""
        return int(self.section("limits")["min_judges_per_persona"])

    @property
    def min_max_rounds(self) -> int:
        """The lowest max_rounds a spec may ask."""
        return int(self.section("limits")["min_max_rounds"])

    @property
    def min_valid_judges(self) -> int:
        """Valid judges needed per persona to aggregate."""
        return int(self.section("limits")["min_valid_judges"])

    @property
    def max_replacements(self) -> int:
        """Rejected attempts allowed per judge slot and round."""
        return int(self.section("limits")["max_replacements"])

    @property
    def severity_caps(self) -> dict[str, int]:
        """The highest score each severity allows."""
        return {k: int(v) for k, v in self.section("severity")["caps"].items()}

    @property
    def severities(self) -> tuple[str, ...]:
        """The severities a judge may give."""
        return tuple(self.severity_caps)

    @property
    def findings(self) -> tuple[str, ...]:
        """The severities written to the author's findings."""
        return tuple(self.section("severity")["findings"])

    @property
    def min_quote_chars(self) -> int:
        """The shortest quote that counts as evidence."""
        return int(self.section("quotes")["min_chars"])

    @property
    def min_fragment_chars(self) -> int:
        """The shortest fragment of a quote split on '...'."""
        return int(self.section("quotes")["min_fragment_chars"])

    @property
    def max_bad_share(self) -> Fraction:
        """The share of unverified quotes that discards a judge."""
        return Fraction(self.section("quotes")["max_bad_share"])

    @property
    def transcript_format(self) -> str:
        """The format of the judges' transcripts: the host adapter that reads them."""
        return str(self.section("transcript")["format"])

    @property
    def judge(self) -> JudgeSettings:
        """The [judge] section."""
        judge: JudgeSettings = self.data["judge"]  # its shape checked against the defaults
        return judge


@cache
def default_config() -> Config:
    """The packaged defaults, as a Config."""
    return Config(default_data())


def load_config(path: Path | None = None) -> Config:
    """The defaults, or the defaults merged with the TOML file at `path`.

    Args:
        path: A TOML file of settings, or None.
    """
    if path is None:
        return default_config()
    try:
        override = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read configuration {path}: {exc}") from exc
    return Config(merge(default_data(), override))


def resolve(cfg: Config | None) -> Config:
    """The configuration given, or the packaged defaults.

    Args:
        cfg: A configuration, or None.
    """
    return default_config() if cfg is None else cfg


# How schema_errors names a key a frozen configuration lacks, and one it holds that the
# defaults do not know.
MISSING_KEY = "missing configuration key"
UNKNOWN_KEY = "unknown configuration key"

# Settings that place files and do not change a grade: never listed as overrides.
NOT_GRADING = frozenset({"mcp.root"})


_ABSENT = object()


def _differences(left: Mapping[str, Any], right: Mapping[str, Any], prefix: str) -> list[str]:
    found = []
    for key in sorted(set(left) | set(right)):
        name = f"{prefix}{key}"
        if name in NOT_GRADING:
            continue
        lv, rv = left.get(key, _ABSENT), right.get(key, _ABSENT)
        if isinstance(lv, Mapping) and isinstance(rv, Mapping):
            found += _differences(lv, rv, name + ".")
        elif lv != rv:
            found.append(name)
    return found


def config_differences(run: Config, server: Config) -> list[str]:
    """The settings of a run that differ from a server's, section by section (never through a
    flattened view a dotted key could shadow); the settings that only place files are left
    aside. Both hold every key: a run's frozen configuration is refused when it lacks one."""
    return _differences(run.data, server.data, "")


def schema_errors(
    data: Mapping[str, Any], shape: Mapping[str, Any] | None = None, path: tuple[str, ...] = ()
) -> list[str]:
    """A frozen configuration holds exactly the keys the defaults know (no default key holds a
    dot, so a dotted key is unknown too): every run of this version froze them all."""
    shape = default_data() if shape is None else shape
    errors = [f"{MISSING_KEY} {'.'.join((*path, k))!r}" for k in shape if k not in data]
    for key, value in data.items():
        where = ".".join((*path, key))
        if key not in shape:
            errors.append(f"{UNKNOWN_KEY} {where!r}")
        elif isinstance(value, Mapping) and isinstance(shape[key], Mapping):
            errors += schema_errors(value, shape[key], (*path, key))
    return errors
