"""The shapes of the records a run writes, and the names the modules that read them share.

Each record is a `TypedDict`: mypy checks every read and write of it, and the file on disk is
the plain JSON it always was. Nothing here converts or validates. A record written by
sealed-panel is read back as it was written (the run's locks and the threat model cover it);
untrusted input is validated where it enters (`rubric.validate_spec` for a spec,
`reply.check_output` for a judge's reply, `settings` for a configuration file), and its type is
given only after that.

These types are not part of the stable API until 1.0: import them from this module. `Spec`,
the one frozen dataclass, is: it is the input a library user builds (`sealed_panel.Spec`).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, fields
from typing import Any, Final, NotRequired, Required, TypedDict

# The spec key that acknowledges the injection flags, frozen and hashed with the rubric.
INJECTION_ACK: Final = "injection_acknowledged"

# The run format, written to run.json at init. The forward rule, for a later 0.4.x release that
# adds a setting frozen into every run (a key of defaults.toml, so of config.json and of the
# configuration's fingerprint), is one of two things:
#   1. bump SCHEMA_VERSION and add its first release to FORMAT_SINCE: a run frozen without the
#      key is then refused as older, and run.refuse_older_run builds that refusal, and the
#      newer-run one, from FORMAT_SINCE, so each names the releases of the run's format; or
#   2. keep the format, and have run.run_config give a run frozen without the key the value
#      that reproduces how that run behaved, after checking config.lock against the file as
#      written, never before.
# Anything else makes a run of this format unreadable by that release (its frozen configuration
# lacks a key), or changes the grade an earlier release would give it. In the other direction,
# a run frozen by a newer release of the same format holds a key this release does not know:
# it is refused, with the advice to upgrade. docs/protocol.md section 11 says the same for
# users.
SCHEMA_VERSION: Final = 1
# The first release that writes each run format: the one table every refusal of a run of
# another format, or of another release of this format, takes its release names from.
FORMAT_SINCE: Final = {1: "0.4.0"}

# The source of a reply read by the judge runner: sealed-panel wrote the brief and read the reply.
RUNNER: Final = "runner"

# -- the frozen spec (spec.json) ----------------------------------------------------------


class Persona(TypedDict):
    """A reader the panel scores as."""

    id: str
    description: str


class Dimension(TypedDict):
    """What a judge scores; `personas` limits it to some personas."""

    id: str
    name: NotRequired[str]
    question: str
    anchors: dict[str, str]
    weight: NotRequired[float]
    personas: NotRequired[list[str]]


class Measured(TypedDict):
    """A criterion an instrument measures, with its numeric bounds when it has them."""

    id: str
    description: str
    max: NotRequired[float]
    min: NotRequired[float]


class DeclaredChoice(TypedDict):
    """A choice the user declared, in their own words."""

    choice: str
    user_said: str


class TemplateRef(TypedDict):
    """The template a spec came from; `sha256` and `modified` are computed at init."""

    id: str
    version: int
    sha256: NotRequired[str]
    modified: NotRequired[bool]


class FrozenSpec(TypedDict, total=False):
    """The spec as frozen into spec.json: validated, with the per-run defaults filled and
    `facts_file` replaced by its text. `injection_acknowledged` is the INJECTION_ACK key."""

    title: Required[str]
    purpose: Required[str]
    personas: Required[list[Persona]]
    dimensions: Required[list[Dimension]]
    framings: Required[list[str]]
    judges_per_persona: Required[int]
    max_rounds: Required[int]
    control_max: Required[float]
    good_min: Required[float]
    noise_floor_min: Required[float]
    facts: str
    measured: list[Measured]
    declared_choices: list[DeclaredChoice]
    template: TemplateRef
    injection_acknowledged: str


# -- a round's keys (keys.json) ------------------------------------------------------------


class Seat(TypedDict):
    """One judge's seat: its persona, stance, label key (label to role), item order and brief
    id."""

    judge: int
    persona: str
    framing: int
    key: dict[str, str]
    order: list[str]
    nonce: str


class RoundKeys(TypedDict):
    """What `brief` froze for a round: the seed, the hashes, the roles and every seat."""

    seed: int
    sealed: bool
    round: int
    rubric_sha256: str
    candidate_hash: str
    roles: list[str]
    injection_flags: list[str]
    injection_flags_by_role: dict[str, list[str]]
    judges: list[Seat]


# -- an attempt (out_j*.json) --------------------------------------------------------------


class WeakPoint(TypedDict):
    """A judge's weakest point on an item. `text`, `quote` and `dimension` are kept as the
    judge wrote them: a reply is not checked beyond its scores, so they are any JSON value."""

    text: object
    dimension: object
    quote: object
    severity: str


class ItemScore(TypedDict):
    """One judge's reading of one item (by role, never by label): the scores kept after the
    quote check and the severity caps, and what the judge wrote. `check_output` writes every
    key; a reader takes a role the brief did not have as `no_item()`."""

    scores: dict[str, int]
    raw_scores: dict[str, int]
    severity: dict[str, str]
    unverified: list[str]
    capped: list[str]
    one_quote: bool
    quotes: dict[str, str]
    retell: object
    weakest_point: WeakPoint
    reasoning: dict[str, object]


def no_item() -> ItemScore:
    """The reading of a role the brief did not have (no previous version in round 1, no
    known-good): every key empty, so a reader needs no special case. A new one each call."""
    return {
        "scores": {},
        "raw_scores": {},
        "severity": {},
        "unverified": [],
        "capped": [],
        "one_quote": False,
        "quotes": {},
        "retell": "",
        "weakest_point": {"text": "", "dimension": None, "quote": "", "severity": "unknown"},
        "reasoning": {},
    }


class Proof(TypedDict, total=False):
    """What the judge runner saw of one process: the command and its bounds, the environment's
    names and salted hashes, and how the process ended. A stale proof (a runner that died) holds
    only `argv`, `started_at`, `stale` and `failure`."""

    brief_sha256: str
    argv: list[str]
    timeout_s: int
    max_reply_bytes: int
    max_stderr_bytes: int
    env_names: list[str]
    env_sha256: dict[str, str] | None
    stderr_log: str
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout_sha256: str
    failure: str
    started_at: float
    stale: bool


class Started(TypedDict, total=False):
    """A runner's attempt before it is recorded (started_j*.json); empty when unreadable."""

    agent_id: str
    argv: list[str]
    started_at: float


class Attempt(TypedDict, total=False):
    """One recorded attempt on a seat: valid, rejected or void. `ok`, `errors` and `items` are
    always there; the rest is added when the attempt is stored."""

    ok: Required[bool]
    errors: Required[list[str]]
    items: Required[dict[str, ItemScore]]
    reply_sha256: str
    judge: int
    agent_id: str
    persona: str
    source: str
    transcript: str | None
    prompt_extra_chars: int
    unreadable_lines: int
    control_mean: float
    void: bool
    replied: bool
    runner: Proof


class StoredAttempt(TypedDict):
    """An attempt's file: the reply as received and its result."""

    raw: str
    result: Attempt


# -- a round's result (result.json) and the ledger view ------------------------------------


# Functional form: `pass` is a keyword.
Measurement = TypedDict("Measurement", {"id": str, "value": str, "pass": bool})
Measurement.__doc__ = "One instrument reading; measured.json holds a list of them."


class DimensionResult(TypedDict):
    """One persona's result on one dimension: the median of the verified candidate scores, and
    the paired, control and baseline medians when the round has them."""

    median: float
    scores: list[int]
    severities: list[str]
    sd: float
    paired_gain: NotRequired[float]
    control_median: NotRequired[float]
    baseline_median: NotRequired[float]


class Finding(TypedDict):
    """A blocking or material reason a judge gave, for the author's findings."""

    judge: int
    dimension: str
    severity: str
    text: object


class PersonaResult(TypedDict):
    """One persona's part of a round's result."""

    judges: list[int]
    dimensions: dict[str, DimensionResult]
    findings: list[Finding]
    control_means: list[float | None]


class RejectedJudge(TypedDict):
    """A rejected attempt, with its reasons."""

    judge: int
    errors: list[str]


class Voided(TypedDict):
    """A void attempt: who, whether it had replied, and why."""

    judge: int
    agent_id: str
    replied: bool
    source: str
    reason: str


class RoundResult(TypedDict):
    """A round's result (result.json): the only record of an aggregated round."""

    round: int
    sealed: bool
    candidate_hash: str | None
    personas: dict[str, PersonaResult]
    composite: float
    noise: float
    best_gain: float | None
    suspect_jumps: list[str]
    rejected_judges: list[RejectedJudge]
    voided: list[Voided]
    sources: list[str]
    injection_flags: list[str]
    injection_flags_by_role: dict[str, list[str]] | None
    runner_env_changed: list[str]
    runner_env_hashes: dict[str, list[str]] | None
    prompt_extras: dict[str, int]
    one_quote_judges: list[int]
    measured: NotRequired[list[Measurement]]


class LedgerView(TypedDict):
    """The run's ledger, derived from its files: the rubric hash and every aggregated round."""

    rubric_sha256: str
    rounds: list[RoundResult]


class Decision(TypedDict, total=False):
    """What the stop rule says comes next (`decide`), as `status` returns it. Not stored: it is
    computed from the ledger each time."""

    decision: Required[str]
    next_round: int
    round: int
    next: str
    why: str | list[str]
    final: RoundResult
    panels: int
    blocked: str
    flags: list[str]
    roles: list[str]
    feedback: str
    overrides: list[str]
    injection_acknowledged: str


# -- the one input type of the API --------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """A spec written in Python, given to `init_run` in place of a spec file. It goes through
    the same checks as a file: `init_run` refuses it with every problem `validate_spec` finds.
    A key left at None (or an empty tuple) is left out of the spec, so the run's defaults
    apply; give `facts` as text (a spec file's `facts_file` has no equivalent here).

    Args:
        title: A neutral title; the judges see it.
        purpose: What a 5 makes happen.
        personas: Who reads the text, each with an `id` and a `description`.
        dimensions: What is scored, each with an `id`, a `question` and `anchors` at 1, 3, 5.
        facts: The facts the judges may trust, as text.
        measured: Criteria an instrument measures, scored by `record_measurement`.
        declared_choices: Choices the user declared, each with `choice` and `user_said`.
        framings: The reading stances rotated across judges.
        judges_per_persona: Judges per persona in each panel.
        max_rounds: The loop's round limit.
        control_max: The highest mean a judge may give the control.
        good_min: The lowest mean a judge may give the known-good.
        noise_floor_min: The lowest noise the stop rule uses.
        template: The template the spec started from (`id` and `version`).
        injection_acknowledged: Why text addressed to a judge belongs in the candidate.
    """

    title: str
    purpose: str
    personas: tuple[Persona, ...]
    dimensions: tuple[Dimension, ...]
    facts: str | None = None
    measured: tuple[Measured, ...] = ()
    declared_choices: tuple[DeclaredChoice, ...] = ()
    framings: tuple[str, ...] = ()
    judges_per_persona: int | None = None
    max_rounds: int | None = None
    control_max: float | None = None
    good_min: float | None = None
    noise_floor_min: float | None = None
    template: TemplateRef | None = None
    injection_acknowledged: str | None = None

    def to_json(self) -> dict[str, Any]:
        """The spec as a spec file holds it: every key given, in field order, sequences as
        lists, and nothing for a key left at None or empty."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is None or value == ():
                continue
            out[f.name] = [copy.deepcopy(v) for v in value] if isinstance(value, tuple) else value
        return out
