"""sealed-panel: grade a text with blind panels of LLM judges and a sealed final panel.

The calling agent (or person) orchestrates; this package refuses. It freezes the rubric,
writes blind judge briefs, validates and records each judge's reply (or runs a configured
judge command once per brief), aggregates per persona and dimension, applies the stop rule,
and reports the grade a sealed panel gave.
"""

from .aggregate import aggregate_round
from .brief import build_brief, write_briefs
from .config import Config, default_config, load_config, overrides
from .decide import decide
from .errors import ConfigError, EvalError
from .record import check_output, last_reply, record_from, record_measurement, record_reply
from .report import render_report, status
from .runner import judge_round, judge_seat, open_seats
from .spec import init_run, load_frozen, validate_spec
from .text import normalise, quote_found

__version__ = "0.3.0"

__all__ = [
    "Config",
    "ConfigError",
    "EvalError",
    "__version__",
    "aggregate_round",
    "build_brief",
    "check_output",
    "decide",
    "default_config",
    "init_run",
    "judge_round",
    "judge_seat",
    "last_reply",
    "load_config",
    "load_frozen",
    "normalise",
    "open_seats",
    "overrides",
    "quote_found",
    "record_from",
    "record_measurement",
    "record_reply",
    "render_report",
    "status",
    "validate_spec",
    "write_briefs",
]
