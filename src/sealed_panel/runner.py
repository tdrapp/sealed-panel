"""The judge runner: one fresh process per brief, the brief on stdin, the reply on stdout.

sealed-panel writes the brief and reads the reply itself, so a runner reply carries its own
proof, stored with it: the sha256 of the brief bytes written, the argument list, the exit
status, the duration, and the sha256 of what came back. The process is started from an
argument list (never a shell), in an empty temporary directory, with an explicit minimal
environment: PATH, HOME and LANG, plus the names in `[judge] env_allow`. Its stderr goes to a
log file beside the brief, never into the reply.

A process that exits with a non-zero status, runs past `[judge] timeout_s`, or writes nothing
gives no reply: the attempt is recorded as void, under the void cap, and never as a reply.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import Config
from .errors import EvalError
from .ledger import results_by_judge
from .record import RUNNER, check_seat, check_void_cap, record_reply, round_keys
from .spec import load_frozen, run_config
from .store import round_dir

# Variables every judge process gets when the parent has them; `[judge] env_allow` adds names.
BASE_ENV = ("PATH", "HOME", "LANG")
# Random bytes in the agent id the runner gives each process.
AGENT_BYTES = 6
# Seconds to collect what a stopped process left in its pipes.
STOP_GRACE_S = 5


def judge_command(cfg: Config) -> list[str]:
    """The run's judge command, or a refusal in host mode."""
    command = [str(arg) for arg in cfg.judge["command"]]
    if not command:
        raise EvalError(
            "this run has no judge command ([judge] command, set with init --config): in host "
            "mode your agent host runs the judges and `record` stores their replies"
        )
    return command


def child_env(cfg: Config, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The judge process's whole environment: PATH, HOME, LANG and the allowed names, when set.
    Nothing else is inherited, so no credential reaches a judge unless the user allowed it."""
    source = os.environ if environ is None else environ
    names = [*BASE_ENV, *cfg.judge["env_allow"]]
    return {n: source[n] for n in names if n in source}


def _runnable(judge: int, prior: list[dict[str, Any]], cfg: Config) -> None:
    """The refusals `record` would apply to a runner reply, checked before a process starts."""
    check_seat(judge, prior, RUNNER, cfg)
    check_void_cap(judge, prior)


def _open_round(run: Path, rnd: int, sealed: bool) -> tuple[Config, Path, dict[str, Any]]:
    load_frozen(run)
    cfg = run_config(run)
    judge_command(cfg)
    rdir = round_dir(run, rnd, sealed)
    keys = round_keys(rdir, rnd)
    if (rdir / "result.json").exists():
        raise EvalError("this round is already aggregated: the runner fills a round before that")
    return cfg, rdir, keys


def open_seats(run: Path, rnd: int, sealed: bool, only: list[int] | None = None) -> list[int]:
    """The judge slots of a round without a valid reply, in order (only those in `only` when
    given). Every refusal is checked here, before any process starts."""
    cfg, rdir, keys = _open_round(run, rnd, sealed)
    slots = [j["judge"] for j in keys["judges"] if isinstance(j, dict)]
    if only:
        unknown = sorted(set(only) - set(slots))
        if unknown:
            raise EvalError(f"no judge {unknown} in {rdir}/keys.json")
        slots = [s for s in slots if s in only]
    by_judge = results_by_judge(rdir)
    seats = []
    for s in slots:
        prior = by_judge.get(s, [])
        if any(r["ok"] for r in prior):
            continue
        _runnable(s, prior, cfg)
        seats.append(s)
    if not seats:
        raise EvalError("every judge asked for already has a valid reply: aggregate the round")
    return seats


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Stop the process and everything it started (its own session on POSIX)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass


def _launch(
    argv: list[str], brief: bytes, timeout: int, env: dict[str, str], log: Path
) -> tuple[int | None, bool, float, bytes]:
    """Run one judge process. Returns (exit status, timed out, seconds, stdout)."""
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="sealed-panel-judge-") as cwd, log.open("wb") as err:
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=err,
                cwd=cwd,
                env=env,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            raise EvalError(f"cannot start the judge command {argv[0]!r}: {exc}") from exc
        timed_out = False
        try:
            out, _ = proc.communicate(brief, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _stop(proc)
            try:
                out, _ = proc.communicate(timeout=STOP_GRACE_S)
            except subprocess.TimeoutExpired:
                out = b""
        return proc.poll(), timed_out, time.monotonic() - start, out


def judge_seat(run: Path, rnd: int, judge: int, sealed: bool) -> dict[str, Any]:
    """Start one fresh judge process on one brief and record what it gave: a reply (valid or
    rejected), or a void when it failed, timed out or wrote nothing. Returns the result."""
    cfg, rdir, keys = _open_round(run, rnd, sealed)
    if not any(isinstance(j, dict) and j.get("judge") == judge for j in keys["judges"]):
        raise EvalError(f"no judge {judge} in {rdir}/keys.json")
    _runnable(judge, results_by_judge(rdir).get(judge, []), cfg)
    argv = judge_command(cfg)
    timeout = int(cfg.judge["timeout_s"])
    env = child_env(cfg)
    brief = (rdir / f"brief_j{judge}.md").read_bytes()
    agent_id = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"
    log = rdir / f"runner_j{judge}_{agent_id}.log"
    code, timed_out, seconds, out = _launch(argv, brief, timeout, env, log)
    reply = out.decode("utf-8", errors="replace")
    if timed_out:
        failure = f"the judge command timed out after {timeout} s"
    elif code != 0:
        failure = f"the judge command exited with status {code}"
    elif not reply.strip():
        failure = "the judge command exited with status 0 without writing a reply"
    else:
        failure = ""
    proof: dict[str, Any] = {
        "brief_sha256": hashlib.sha256(brief).hexdigest(),
        "argv": argv,
        "exit_code": code,
        "timed_out": timed_out,
        "timeout_s": timeout,
        "duration_s": round(seconds, 3),
        "stdout_sha256": hashlib.sha256(out).hexdigest(),
        "env_names": sorted(env),
        "stderr_log": log.name,
    }
    if failure:
        proof["failure"] = failure
    return record_reply(
        run, rnd, judge, agent_id, reply, sealed, RUNNER, void=bool(failure), runner=proof
    )


def judge_round(
    run: Path, rnd: int, sealed: bool, only: list[int] | None = None
) -> list[dict[str, Any]]:
    """Run the judge command once for every open seat of a round, one after the other, and
    return each recorded result. A refusal stops the round; what was recorded stays."""
    return [judge_seat(run, rnd, j, sealed) for j in open_seats(run, rnd, sealed, only)]
