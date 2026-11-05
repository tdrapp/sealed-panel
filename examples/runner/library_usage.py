"""A one-shot grade from Python: freeze, brief the sealed panel, let the judge command answer,
aggregate, print the report. The same functions the command line and the MCP server call.

    python library_usage.py RUN --spec SPEC.json --config JUDGE.toml \\
        --artefact TEXT.md --control CONTROL.md

JUDGE.toml holds the `[judge]` section (see judge.toml beside this file). A loop run is the
same calls with `sealed=False` and a round number, revising between rounds while `status`
says CONTINUE.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sealed_panel import (
    EvalError,
    aggregate_round,
    init_run,
    judge_round,
    load_config,
    render_report,
    status,
    write_briefs,
)


def grade(run: Path, spec: Path, config: Path, artefact: Path, control: Path) -> str:
    """Grade one text once with a sealed panel and return the report."""
    init_run(run, spec, load_config(config))
    write_briefs(run, 1, artefact, control, None, True)
    for result in judge_round(run, 1, True):
        if not result["ok"]:
            # A rejected or void judge: run its seat again with a fresh process.
            judge_round(run, 1, True, [result["judge"]])
    aggregate_round(run, 1, True)
    if status(run)["decision"] != "SEALED":
        raise EvalError("the sealed panel did not aggregate")
    return render_report(run)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A one-shot sealed grade through the library.")
    ap.add_argument("run", type=Path)
    ap.add_argument("--spec", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--artefact", type=Path, required=True)
    ap.add_argument("--control", type=Path, required=True)
    a = ap.parse_args(argv)
    try:
        print(grade(a.run, a.spec, a.config, a.artefact, a.control), end="")
    except EvalError as exc:
        print(f"library_usage: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
