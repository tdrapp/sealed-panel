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
under the void cap, and never as a reply. A seat is held by an in-progress marker from its
check to its record, so concurrent calls never judge one seat twice, and a marker a stopped
runner left is recorded as a void by the next call.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .config import Config
from .errors import EvalError
from .ledger import results_by_judge
from .record import RUNNER, _record_reply, check_seat, check_void_cap, round_keys
from .spec import load_frozen, run_config, run_salt
from .store import read_no_follow, round_dir

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
# A seat's in-progress marker, beside its brief: running_j<J>.json.
MARKER_PREFIX = "running_j"
# A marker moved aside by the call settling it, until its void is recorded.
STALE_SUFFIX = ".stale"
# Seconds between looks at a running judge.
POLL_S = 0.02
# Seconds past the timeout after which a marker whose holder still runs counts as stale.
STALE_MARGIN_S = 60


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
    if (rdir / "result.json").exists():
        raise EvalError("this round is already aggregated: the runner fills a round before that")
    return cfg, rdir, keys


class SeatTaken(EvalError):
    """A seat another call is judging right now, or one that already holds a valid reply."""


def _marker(rdir: Path, judge: int) -> Path:
    return rdir / f"{MARKER_PREFIX}{judge}.json"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True  # it exists, under another user
    return True


def _settle(run: Path, rnd: int, judge: int, sealed: bool, rdir: Path, cfg: Config) -> None:
    """Deal with the seat's in-progress marker. A live one (its holder runs, within the timeout
    and a margin) means another call is judging the seat: SeatTaken. A stale one was left by a
    runner that stopped before recording: its attempt is recorded as a void, then the marker
    goes. The marker is first moved to a unique name, so two calls never record it twice."""
    path = _marker(rdir, judge)
    try:
        text = read_no_follow(path).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return
    except EvalError:
        path.unlink(missing_ok=True)  # a link or a FIFO: no runner writes one
        return
    try:
        held = json.loads(text)
    except ValueError:
        held = {}
    if not isinstance(held, dict):
        held = {}
    pid, started = held.get("pid"), held.get("started")
    limit = int(cfg.judge["timeout_s"]) + STALE_MARGIN_S
    fresh = isinstance(started, (int, float)) and time.time() - started < limit
    if isinstance(pid, int) and _alive(pid) and fresh:
        raise SeatTaken(f"judge {judge} is being judged by another call (pid {pid}): wait for it")
    taken = path.with_name(f"{path.name}.{secrets.token_hex(AGENT_BYTES)}{STALE_SUFFIX}")
    try:
        path.rename(taken)
    except FileNotFoundError:
        return  # another call settled it first
    if read_no_follow(taken).decode("utf-8", errors="replace") != text:
        # Another call settled the stale marker and took the seat in between: put its live
        # marker back, unless yet another call holds the seat by now.
        with contextlib.suppress(FileExistsError):
            os.link(taken, path)
        taken.unlink(missing_ok=True)
        raise SeatTaken(f"judge {judge} is being judged by another call")
    _record_stale(run, rnd, judge, sealed, taken, held)


# What a void keeps from a marker: who held the seat and since when, nothing it could forge.
MARKER_KEYS = ("pid", "started", "argv")


def _record_stale(
    run: Path, rnd: int, judge: int, sealed: bool, taken: Path, held: dict[str, Any]
) -> None:
    """A stopped runner's attempt, recorded as a void under a fresh agent id and the void cap.
    A marker is a file anyone with access to the run can write, so the void takes nothing from
    it but its holder and start time. A seat that already has a valid reply takes no void."""
    try:
        if any(r["ok"] for r in results_by_judge(taken.parent).get(judge, [])):
            return
        agent = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"
        failure = "the runner that started this judge stopped before recording it"
        kept = {k: held[k] for k in MARKER_KEYS if k in held}
        proof = {**kept, "stale": True, "failure": failure}
        try:
            _record_reply(run, rnd, judge, agent, "", sealed, RUNNER, void=True, runner=proof)
        except EvalError:
            # At the void cap, or a round closed: the seat already says why; another seat's
            # judge must not fail for it.
            return
    finally:
        taken.unlink(missing_ok=True)


def settle_stale_files(run: Path, rnd: int, sealed: bool) -> None:
    """Markers already moved aside by a runner killed right after the move: record them. Only
    a run with a judge command has markers; elsewhere such a file is ignored. One for a seat
    the round does not have is dropped."""
    cfg = run_config(run)
    if not cfg.judge["command"]:
        return
    rdir = round_dir(run, rnd, sealed)
    seats = {j.get("judge") for j in round_keys(rdir, rnd)["judges"] if isinstance(j, dict)}
    for taken in sorted(rdir.glob(f"{MARKER_PREFIX}*{STALE_SUFFIX}")):
        found = re.match(rf"{MARKER_PREFIX}(\d+)\.json\.", taken.name)
        if found is None or int(found.group(1)) not in seats:
            taken.unlink(missing_ok=True)
            continue
        try:
            held = json.loads(read_no_follow(taken))
        except EvalError:
            taken.unlink(missing_ok=True)  # a link or a FIFO: no runner writes one
            continue
        except ValueError:
            held = {}
        held = held if isinstance(held, dict) else {}
        _record_stale(run, rnd, int(found.group(1)), sealed, taken, held)


def open_seats(
    run: Path,
    rnd: int,
    sealed: bool,
    only: list[int] | None = None,
    expected: Mapping[str, Any] | None = None,
) -> list[int]:
    """The judge slots of a round without a valid reply and not being judged by another call,
    in order (only those in `only` when given). A marker a stopped runner left is recorded as
    a void first. Every refusal is checked here, before any process starts. `expected` is the
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
    settle_stale_files(run, rnd, sealed)
    slots = [j["judge"] for j in keys["judges"] if isinstance(j, dict)]
    if only:
        unknown = sorted(set(only) - set(slots))
        if unknown:
            raise EvalError(f"no judge {unknown} in {rdir}/keys.json")
        slots = [s for s in slots if s in only]
    busy = []
    for s in slots:
        try:
            _settle(run, rnd, s, sealed, rdir, cfg)
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
            "every judge asked for already has a valid reply or is being judged by another call"
            + (f" ({busy})" if busy else ": aggregate the round")
        )
    return seats


def _stop(proc: subprocess.Popen[bytes]) -> None:
    """Stop the process and everything it started (its own session on POSIX). macOS refuses
    to signal a group whose members are all exited (EPERM): the process itself is then killed,
    and a process that left the session is out of reach."""
    if os.name == "posix":
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
        log.open("wb") as err,
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
                start_new_session=os.name == "posix",
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
    refused. The seat is held by an in-progress marker from the check to the record, so two
    calls never judge it at once; every process started ends recorded. Returns the result.

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
    _settle(run, rnd, judge, sealed, rdir, cfg)
    agent_id = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"
    argv = judge_command(cfg)
    held = {"pid": os.getpid(), "agent_id": agent_id, "started": time.time(), "argv": argv}
    marker = _marker(rdir, judge)
    # Written whole under a unique name, then linked into place: the link fails when the marker
    # exists, and no reader ever sees a marker without its holder.
    draft = marker.with_name(f"{marker.name}.{agent_id}.new")
    draft.write_text(json.dumps(held), encoding="utf-8")
    try:
        os.link(draft, marker)
    except FileExistsError as exc:
        raise SeatTaken(f"judge {judge} is being judged by another call") from exc
    finally:
        draft.unlink(missing_ok=True)
    try:
        prior = results_by_judge(rdir).get(judge, [])
        if any(r["ok"] for r in prior):
            raise SeatTaken(
                f"judge {judge} already has a valid reply in this round: "
                "a second one would be a re-roll"
            )
        _runnable(judge, prior, cfg)
        brief = read_no_follow(rdir / f"brief_j{judge}.md")
    except BaseException:
        marker.unlink(missing_ok=True)
        raise
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
    try:
        code, timed_out, over, seconds, out = _launch(
            argv, brief, timeout, (cap, cap_err), env, log
        )
    except EvalError:
        marker.unlink(missing_ok=True)  # the command never started: nothing to record
        raise
    except BaseException as exc:
        # Interrupted while the judge ran: record the attempt, then stop. If even that fails,
        # the marker stays, and the next call records it as a void.
        proof["failure"] = f"the runner was interrupted ({type(exc).__name__})"
        _record_reply(run, rnd, judge, agent_id, "", sealed, RUNNER, void=True, runner=proof)
        marker.unlink(missing_ok=True)
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
    marker.unlink(missing_ok=True)
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
