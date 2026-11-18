"""sealed-panel: grade a text with blind panels of LLM judges and a sealed final panel.

The calling agent (or person) orchestrates; this package refuses. It freezes the rubric,
writes blind judge briefs, validates and records each judge's reply (or runs a configured
judge command once per brief), aggregates per persona and dimension, applies the stop rule,
and reports the grade a sealed panel gave.

`__all__` is the stable API (docs/api.md): the steps a library user calls, the configuration,
the spec it may build in Python, and every exception a caller must catch. Everything else,
such as `rubric.validate_spec`, `reply.check_output`, `text.normalise` or the record types in
`model`, imports from its module and may change before 1.0.
"""

from .brief import write_briefs
from .errors import (
    ConfigError,
    EvalError,
    FormatError,
    MalformedReply,
    PanelIncomplete,
    SeatTaken,
)
from .measure import record_measurement
from .model import SCHEMA_VERSION, Spec
from .record import record_from, record_reply
from .report import render_report, status
from .result import GradeResult
from .runner import judge_round
from .settings import Config, load_config
from .steps import aggregate_round, grade, init_run

__version__ = "0.4.0"

__all__ = [
    "SCHEMA_VERSION",
    "Config",
    "ConfigError",
    "EvalError",
    "FormatError",
    "GradeResult",
    "MalformedReply",
    "PanelIncomplete",
    "SeatTaken",
    "Spec",
    "__version__",
    "aggregate_round",
    "grade",
    "init_run",
    "judge_round",
    "load_config",
    "record_from",
    "record_measurement",
    "record_reply",
    "render_report",
    "status",
    "write_briefs",
]
