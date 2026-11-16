"""Host adapters: one per transcript format, in a closed registry.

The core reads a judge's transcript only through `read_transcript` (and the adapter `adapter`
returns), which picks the adapter the run's `[transcript] format` names; it never imports a host
module by name. A new host is a new module here and a new entry in `FORMATS`, never a setting.
"""

from __future__ import annotations

from pathlib import Path

from ..settings import Config, resolve
from ..transcript import Adapter, Transcript
from . import agent_jsonl, rollout_jsonl

# Every format sealed-panel reads, by the id `[transcript] format` takes. Closed: a format is
# added in code, with its tests, never by a setting.
FORMATS: dict[str, Adapter] = {
    "agent-jsonl": Adapter(agent_jsonl.read, agent_jsonl.siblings, agent_jsonl.launchers),
    "rollout-jsonl": Adapter(rollout_jsonl.read, rollout_jsonl.siblings, rollout_jsonl.launchers),
}


def adapter(cfg: Config | None = None) -> Adapter:
    """The adapter of the configuration's transcript format.

    Args:
        cfg: The run's configuration, or None for the defaults.
    """
    return FORMATS[resolve(cfg).transcript_format]


def read_transcript(path: Path, cfg: Config | None = None) -> Transcript:
    """Read a judge's transcript with the adapter of the configuration's format.

    Args:
        path: The transcript file.
        cfg: The run's configuration, or None for the defaults.
    """
    return adapter(cfg).read(path)
