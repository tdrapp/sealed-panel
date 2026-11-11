"""The judge runner: one fresh process per brief, the brief on stdin, the reply on stdout.

sealed-panel writes the brief and reads the reply itself, so a runner reply carries its own
proof, stored with it: the sha256 of the brief bytes written, the argument list, the exit
status, the duration, the sha256 of what came back, and the sha256 of the value of each
name in `[judge] env_allow` it passed, salted with the run's own random salt (never the value:
it may be a credential). The process is
started from an argument list (never a shell), in an empty temporary directory, with an
explicit minimal environment: PATH, HOME and LANG, plus the names in `[judge] env_allow`. Its
stderr goes to a log file beside the brief, never into the reply.

A process that exits with a non-zero status, runs past `[judge] timeout_s`, writes nothing, or
writes more than `[judge] max_reply_bytes` gives no reply: the attempt is recorded as void,
under the void cap, and never as a reply. A seat is held by a lock (fcntl.flock on
`seat_j<J>.lock`) from its check to its record, so concurrent calls never judge one seat twice.
Before its process starts, the runner writes a started record; a runner that died before
recording leaves it behind (the kernel releases its lock), and the next call records it as a
void.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from .config import Config
from .errors import EvalError, SeatTaken
from .ledger import results_by_judge
from .record import RUNNER, _record_reply, check_seat, check_void_cap, round_keys, seat_lock
from .spec import load_frozen, run_config, run_salt
from .store import dump_json, load_json, round_dir

# Variables every judge process gets when the parent has them; `[judge] env_allow` adds names.
BASE_ENV = ("PATH", "HOME", "LANG")
# Random bytes in the agent id the runner gives each process.
AGENT_BYTES = 6
# Seconds to collect what a stopped process left in its pipes.
STOP_GRACE_S = 5
# Bytes read from a judge's stdout at a time.
READ_CHUNK = 65536
# A run frozen before the output caps existed gets the default.
DEFAULT_MAX_BYTES = 1 << 20
# A runner's attempt, beside its brief from before its process starts until it is recorded:
# started_j<J>_<agent id>.json.
STARTED_PREFIX = "started_j"
# Seconds between looks at a running judge.
POLL_S = 0.02


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


def env_hashes(cfg: Config, env: Mapping[str, str], salt: bytes | None) -> dict[str, str] | None:
    """The salted sha256 of the value of each `[judge] env_allow` name the judge process gets.

    The value is never stored: it may be a credential. The salt is the run's own, random, so a
    hash cannot be looked up in a table made in advance nor matched across runs. Two attempts
    whose hashes differ ran under different values (another model, another key), which the
    report flags.

    Args:
        cfg: The run's configuration.
        env: The judge process's environment.
        salt: The run's salt; None for a run frozen without one.

    Returns:
        The hash of each allowed name that is set, by name; None without a salt, so nothing is
        compared.
    """
    if salt is None:
        return None
    allowed = cfg.judge["env_allow"]
    return {
        n: hashlib.sha256(salt + env[n].encode("utf-8")).hexdigest() for n in allowed if n in env
    }


def _runnable(judge: int, prior: list[dict[str, Any]], cfg: Config) -> None:
    """The refusals `record` would apply to a runner reply, checked before a process starts."""
    check_seat(judge, prior, RUNNER, cfg)
    check_void_cap(judge, prior)


def check_judge(cfg: Config, expected: Mapping[str, Any] | None) -> None:
    """Refuse a run whose frozen judge settings differ from `expected`, the [judge] section of
    the configuration the user started the MCP server with: the caller never picks its judge."""
    if expected is None:
        return
    frozen = {k: cfg.judge.get(k) for k in expected}
    if frozen != dict(expected):
        raise EvalError(
            "this run's judge settings differ from the configuration the server was started "
            f"with: run {json.dumps(frozen)}, server {json.dumps(dict(expected))}"
        )


def _open_round(
    run: Path, rnd: int, sealed: bool, expected: Mapping[str, Any] | None = None
) -> tuple[Config, Path, dict[str, Any]]:
    load_frozen(run)
    cfg = run_config(run)
    judge_command(cfg)
    check_judge(cfg, expected)
    rdir = round_dir(run, rnd, sealed)
    keys = round_keys(rdir, rnd)
    _refuse_closed(rdir)
    return cfg, rdir, keys


def _refuse_closed(rdir: Path) -> None:
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: the runner fills a round before that")


# What a void keeps from a started record: the command and its start time, nothing else.
STARTED_KEYS = ("argv", "started_at")


def _settle(run: Path, rnd: int, judge: int, sealed: bool, rdir: Path) -> None:
    """Under the seat's lock: each started record left there belongs to a runner that died
    (a live one would hold the lock). When its attempt is already stored (the runner died
    between storing it and removing the record), the record just goes. Otherwise the attempt is
    recorded as a void, under a fresh agent id and the void cap, then the record goes. A seat
    with a valid reply takes no void, and a seat at its cap is left as it is."""
    for path in sorted(rdir.glob(f"{STARTED_PREFIX}{judge}_*.json")):
        held = _started(path)
        stored = {r.get("agent_id") for r in results_by_judge(rdir).get(judge, [])}
        if held.get("agent_id") not in stored:
            _record_stale(run, rnd, judge, sealed, rdir, held)
        path.unlink(missing_ok=True)


def _started(path: Path) -> dict[str, Any]:
    """A started record's content; an unreadable one (cut short by a power loss) is empty, so
    its attempt still becomes a void, with nothing taken from it."""
    try:
        held = load_json(path)
    except EvalError:
        return {}
    return held if isinstance(held, dict) else {}


def _record_stale(
    run: Path, rnd: int, judge: int, sealed: bool, rdir: Path, held: dict[str, Any]
) -> None:
    """A dead runner's attempt, recorded as a void."""
    if any(r["ok"] for r in results_by_judge(rdir).get(judge, [])):
        return
    agent = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"
    failure = "the runner that started this judge stopped before recording it"
    kept = {k: held[k] for k in STARTED_KEYS if k in held}
    proof = {**kept, "stale": True, "failure": failure}
    try:
        _record_reply(run, rnd, judge, agent, "", sealed, RUNNER, void=True, runner=proof)
    except EvalError:
        # At the void cap, or a round closed: the seat already says why; another seat's
        # judge must not fail for it.
        return


@contextlib.contextmanager
def every_seat(run: Path, rnd: int, sealed: bool) -> Iterator[None]:
    """Hold every seat of a round while the block runs, as the aggregate step does: refused
    while any seat is held by another call (a judge running or a reply being recorded). In a
    run with a judge command, a dead runner's attempt is first recorded as a void.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
    """
    cfg = run_config(run)
    rdir = round_dir(run, rnd, sealed)
    seats = [j["judge"] for j in round_keys(rdir, rnd)["judges"] if isinstance(j, dict)]
    with contextlib.ExitStack() as held:
        for seat in seats:
            held.enter_context(seat_lock(rdir, seat))
            if cfg.judge["command"]:
                _settle(run, rnd, seat, sealed, rdir)
        yield


def open_seats(
    run: Path,
    rnd: int,
    sealed: bool,
    only: list[int] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> list[int]:
    """The judge slots of a round without a valid reply and not held by another call,
    in order (only those in `only` when given). A dead runner's attempt is recorded as a void
    first. Every refusal is checked here, before any process starts. `expected` is the
    judge section the caller must match (see check_judge).

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
        only: The judge slots to consider, or None for all.
        expected: The judge section the caller must match (the MCP server's own), or None to take
            the run's.
    """
    cfg, rdir, keys = _open_round(run, rnd, sealed, expected)
    slots = [j["judge"] for j in keys["judges"] if isinstance(j, dict)]
    if only:
        unknown = sorted(set(only) - set(slots))
        if unknown:
            raise EvalError(f"no judge {unknown} in {rdir}/keys.json")
        slots = [s for s in slots if s in only]
    busy = []
    for s in slots:
        try:
            with seat_lock(rdir, s):
                _settle(run, rnd, s, sealed, rdir)
        except SeatTaken:
            busy.append(s)
    by_judge = results_by_judge(rdir)
    seats = []
    for s in slots:
        prior = by_judge.get(s, [])
        if s in busy or any(r["ok"] for r in prior):
            continue
        _runnable(s, prior, cfg)
        seats.append(s)
    if not seats:
        raise EvalError(
            "every judge asked for already has a valid reply or its seat is held by another call"
            + (f" ({busy})" if busy else ": aggregate the round")
        )
    return seats


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Stop the process and everything it started (its own session). macOS refuses to signal a
    group whose members are all exited (EPERM): the process itself is then killed, and a process
    that left the session is out of reach."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
        return
    except OSError:
        pass
    with contextlib.suppress(OSError):
        proc.kill()


def _pump(fd: int, cap: int, sink: Callable[[bytes], Any], over: threading.Event) -> None:
    """Copy a pipe to `sink` up to `cap` bytes; past it, set `over` and stop reading."""
    size = 0
    while chunk := os.read(fd, READ_CHUNK):
        sink(chunk[: max(cap - size, 0)])
        size += len(chunk)
        if size > cap:
            over.set()
            return


def _launch(
    argv: list[str],
    brief: bytes,
    timeout: int,
    caps: tuple[int, int],
    env: dict[str, str],
    log: Path,
) -> tuple[int | None, bool, str, float, bytes]:
    """Run one judge process: the brief on stdin, stdout and stderr read in chunks up to their
    caps. Returns (exit status, timed out, the stream over its cap or "", seconds, stdout). Past
    the timeout or a cap, or on any interruption, the process group is stopped."""
    start = time.monotonic()
    with (
        tempfile.TemporaryDirectory(prefix="sealed-panel-judge-") as cwd,
        tempfile.TemporaryFile() as stdin,
        os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as err,
    ):
        stdin.write(brief)
        stdin.seek(0)
        try:
            # The user's judge command, as an argument list and never through a shell.
            proc = subprocess.Popen(  # noqa: S603
                argv,
                stdin=stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise EvalError(f"cannot start the judge command {argv[0]!r}: {exc}") from exc
        assert proc.stdout is not None and proc.stderr is not None  # noqa: S101 (typing only)
        chunks: list[bytes] = []
        over_out, over_err = threading.Event(), threading.Event()
        reader = threading.Thread(
            target=_pump, args=(proc.stdout.fileno(), caps[0], chunks.append, over_out), daemon=True
        )
        logger = threading.Thread(
            target=_pump, args=(proc.stderr.fileno(), caps[1], err.write, over_err), daemon=True
        )
        reader.start()
        logger.start()
        timed_out = False
        try:
            while not (over_out.is_set() or over_err.is_set()):
                if not reader.is_alive() and proc.poll() is not None:
                    break  # the output is closed and the process is gone
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    break
                time.sleep(POLL_S)
        except BaseException:
            _stop(proc)
            raise
        if timed_out or over_out.is_set() or over_err.is_set():
            _stop(proc)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(STOP_GRACE_S)
        # Left behind when a process outside the group holds a stream.
        reader.join(STOP_GRACE_S)
        logger.join(STOP_GRACE_S)
        over = "stdout" if over_out.is_set() else "stderr" if over_err.is_set() else ""
        return proc.poll(), timed_out, over, time.monotonic() - start, b"".join(list(chunks))


def judge_seat(
    run: Path, rnd: int, judge: int, sealed: bool, expected: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Start one fresh judge process on one brief and record what it gave: a reply (valid or
    rejected), or a void when it failed, timed out, wrote nothing or too much, or its reply was
    refused. The seat's lock is held from the check to the record, so two calls never judge it
    at once (the second gets SeatTaken); every process started ends recorded. Returns the result.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        judge: The judge slot.
        sealed: Whether the round is the sealed panel's.
        expected: The judge section the caller must match (the MCP server's own), or None to take
            the run's.
    """
    cfg, rdir, keys = _open_round(run, rnd, sealed, expected)
    if not any(isinstance(j, dict) and j.get("judge") == judge for j in keys["judges"]):
        raise EvalError(f"no judge {judge} in {rdir}/keys.json")
    with seat_lock(rdir, judge):
        return _judge_held(run, rnd, judge, sealed, rdir, cfg)


def _judge_held(
    run: Path, rnd: int, judge: int, sealed: bool, rdir: Path, cfg: Config
) -> dict[str, Any]:
    """judge_seat, under the seat's lock."""
    _refuse_closed(rdir)  # aggregated since the first check
    _settle(run, rnd, judge, sealed, rdir)
    prior = results_by_judge(rdir).get(judge, [])
    if any(r["ok"] for r in prior):
        raise SeatTaken(
            f"judge {judge} already has a valid reply in this round: "
            "a second one would be a re-roll"
        )
    _runnable(judge, prior, cfg)
    brief = (rdir / f"brief_j{judge}.md").read_bytes()
    agent_id = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"
    argv = judge_command(cfg)
    timeout = int(cfg.judge["timeout_s"])
    cap = int(cfg.judge.get("max_reply_bytes", DEFAULT_MAX_BYTES))
    cap_err = int(cfg.judge.get("max_stderr_bytes", DEFAULT_MAX_BYTES))
    env = child_env(cfg)
    log = rdir / f"runner_j{judge}_{agent_id}.log"
    proof: dict[str, Any] = {
        "brief_sha256": hashlib.sha256(brief).hexdigest(),
        "argv": argv,
        "timeout_s": timeout,
        "max_reply_bytes": cap,
        "max_stderr_bytes": cap_err,
        "env_names": sorted(env),
        "env_sha256": env_hashes(cfg, env, run_salt(run)),
        "stderr_log": log.name,
    }
    started = rdir / f"{STARTED_PREFIX}{judge}_{agent_id}.json"
    dump_json(started, {"agent_id": agent_id, "argv": argv, "started_at": time.time()})
    try:
        code, timed_out, over, seconds, out = _launch(
            argv, brief, timeout, (cap, cap_err), env, log
        )
    except EvalError:
        started.unlink(missing_ok=True)  # the command never started: nothing to record
        raise
    except BaseException as exc:
        # Interrupted while the judge ran: record the attempt, then stop. If even that fails,
        # the started record stays, and the next call records it as a void.
        proof["failure"] = f"the runner was interrupted ({type(exc).__name__})"
        _record_reply(run, rnd, judge, agent_id, "", sealed, RUNNER, void=True, runner=proof)
        started.unlink(missing_ok=True)
        raise
    reply = out.decode("utf-8", errors="replace")
    if timed_out:
        failure = f"the judge command timed out after {timeout} s"
    elif over == "stdout":
        failure = f"the judge command wrote more than {cap} bytes"
    elif over:
        failure = f"the judge command wrote more than {cap_err} bytes to stderr"
    elif code != 0:
        failure = f"the judge command exited with status {code}"
    elif not reply.strip():
        failure = "the judge command exited with status 0 without writing a reply"
    else:
        failure = ""
    proof |= {
        "exit_code": code,
        "timed_out": timed_out,
        "duration_s": round(seconds, 3),
        "stdout_sha256": hashlib.sha256(out).hexdigest(),
    }
    if failure:
        proof["failure"] = failure
    try:
        result = _record_reply(
            run, rnd, judge, agent_id, reply, sealed, RUNNER, void=bool(failure), runner=proof
        )
    except (EvalError, ValueError, RecursionError) as exc:
        if failure:
            raise
        # A reply the record refuses (one already recorded) still ends recorded, as a void.
        proof["failure"] = f"the reply was refused: {exc}"
        result = _record_reply(
            run, rnd, judge, agent_id, reply, sealed, RUNNER, void=True, runner=proof
        )
    started.unlink(missing_ok=True)
    return result


def judge_round(
    run: Path,
    rnd: int,
    sealed: bool,
    only: list[int] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run the judge command once for every open seat of a round, one after the other, and
    return each recorded result. A seat another call takes meanwhile is skipped; any other
    refusal stops the round, and what was recorded stays.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        sealed: Whether the round is the sealed panel's.
        only: The judge slots to run, or None for every open one.
        expected: The judge section the caller must match (the MCP server's own), or None to take
            the run's.
    """
    results = []
    for seat in open_seats(run, rnd, sealed, only, expected):
        try:
            results.append(judge_seat(run, rnd, seat, sealed, expected))
        except SeatTaken:
            continue
    return results
