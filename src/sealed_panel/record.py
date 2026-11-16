"""Record one judge's attempt for a round: a reply, a rejected reply or a void.

A reply comes either from a file the judge's text was saved to, or from a transcript (see
`transcript`). It is validated by `reply`, checked against its seat by `seat`, and stored in
the round's directory.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .errors import EvalError, MalformedReply
from .hosts import read_transcript
from .ledger import results_by_judge, used_agent_ids, used_reply_hashes
from .model import RUNNER, Attempt, Proof, RoundKeys, Seat
from .reply import check_output, extract_json
from .rubric import dims_for
from .run import load_frozen, refuse_older_run, run_config
from .seat import calibrate, check_seat, check_void_cap, seat_lock
from .settings import Config
from .store import dump_json, load_json, read_text, round_dir, under_run_lock
from .text import normalise
from .transcript import Transcript, reply_text

# Widths used in a recorded reply's file name.
AGENT_TAG_CHARS = 12
SAFE_NAME_CHARS = 40


def safe_name(s: str) -> str:
    """A string fit for a file name, cut short.

    Args:
        s: The string.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:SAFE_NAME_CHARS]


def _store(rdir: Path, judge: int, agent_id: str, raw: str, result: Attempt) -> None:
    tag = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:AGENT_TAG_CHARS]
    dump_json(
        rdir / f"out_j{judge}_{safe_name(agent_id)}_{tag}.json", {"raw": raw, "result": result}
    )


def _void(
    rdir: Path,
    judge: int,
    agent_id: str,
    meta: Seat,
    t: Transcript,
    prior: list[Attempt],
) -> Attempt:
    """An agent that received a brief and stopped without a reply: recorded as void so the
    round can be aggregated. A void clears the unrecorded-agent block but never fills a seat.
    Refused when the agent got the whole brief and left any reply: that reply is recorded."""
    brief = normalise(read_text(rdir / f"brief_j{judge}.md"))
    pasted = brief in normalise(t.prompt)
    replied = t.replied
    if pasted and replied:
        raise EvalError(
            "this judge got the brief and replied: record its reply, it cannot be voided"
        )
    check_void_cap(judge, prior)
    result: Attempt = {
        "ok": False,
        "void": True,
        "replied": replied,
        "errors": [
            "voided after replying: the brief was not verbatim in its prompt"
            if replied
            else "no reply: the agent stopped before answering"
        ],
        "items": {},
        "judge": judge,
        "agent_id": agent_id,
        "persona": meta["persona"],
        "source": "void",
        "transcript": str(t.path),
        "reply_sha256": hashlib.sha256(f"void:{agent_id}".encode()).hexdigest(),
    }
    _store(rdir, judge, agent_id, "", result)
    return result


def _runner_void(
    rdir: Path,
    judge: int,
    agent_id: str,
    meta: Seat,
    raw: str,
    proof: Proof,
    prior: list[Attempt],
) -> Attempt:
    """A judge process that failed or timed out: the brief was delivered, no reply came back.
    Recorded as void, under the void cap; whatever it wrote is kept for a human, never read."""
    check_void_cap(judge, prior)
    result: Attempt = {
        "ok": False,
        "void": True,
        "replied": bool(raw.strip()),
        "errors": [str(proof["failure"])],
        "items": {},
        "judge": judge,
        "agent_id": agent_id,
        "persona": meta["persona"],
        "source": RUNNER,
        "transcript": None,
        "runner": proof,
        "reply_sha256": hashlib.sha256(f"void:{agent_id}".encode()).hexdigest(),
    }
    _store(rdir, judge, agent_id, raw, result)
    return result


def round_keys(rdir: Path, rnd: int) -> RoundKeys:
    """The round's keys.json, refused when the round is not briefed or the file is damaged.

    Args:
        rdir: The round directory.
        rnd: The round number, from 1.
    """
    path = rdir / "keys.json"
    if not path.exists():
        raise EvalError(f"round {rnd} is not briefed ({path} is missing)")
    keys: RoundKeys = load_json(path)
    if not isinstance(keys, dict) or not isinstance(keys.get("judges"), list):
        raise EvalError(f"{path} is damaged: no list of judges")
    return keys


def _judge_meta(keys: RoundKeys, judge: int, rdir: Path) -> Seat:
    meta = next(
        (j for j in keys["judges"] if isinstance(j, dict) and j.get("judge") == judge), None
    )
    if meta is None:
        raise EvalError(f"no judge {judge} in {rdir}/keys.json")
    return meta


def _refuse_in_a_runner_run(cfg: Config) -> None:
    """A run with a judge command takes runner replies only, on every seat."""
    if cfg.judge["command"]:
        raise EvalError(
            "this run has a judge command: every reply comes from the runner "
            "(`sealed-panel judge`), never pasted, read from a transcript or voided by hand"
        )


def record_reply(
    run: Path,
    rnd: int,
    judge: int,
    agent_id: str,
    raw: str,
    sealed: bool,
    source: str = "output",
    transcript: Path | None = None,
    void: bool = False,
    malformed: str = "",
) -> Attempt:
    """Validate and store one judge's reply, or a void attempt. Returns the result. A
    non-empty `malformed` records the attempt as rejected with that reason. A runner reply is
    recorded only by the runner itself, which wrote the brief and read the reply.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        judge: The judge slot.
        agent_id: The id of the judge's context, new for every reply in the run.
        raw: The reply as the judge wrote it.
        sealed: Whether the round is the sealed panel's.
        source: Where the reply came from: output, transcript or void.
        transcript: The judge's transcript, when the reply was read there.
        void: Record an attempt that never replied.
        malformed: A reason to record the attempt as rejected, or empty.
    """
    if source == RUNNER:
        raise EvalError("a runner reply is recorded by `sealed-panel judge` only, with its proof")
    refuse_older_run(run)  # before the seat's lock file is created
    rdir = round_dir(run, rnd, sealed)
    _judge_meta(round_keys(rdir, rnd), judge, rdir)  # a seat that exists, before its lock file
    with seat_lock(rdir, judge):
        return record_attempt(
            run, rnd, judge, agent_id, raw, sealed, source, transcript, void, malformed
        )


def _read_proof(
    rdir: Path, judge: int, transcript: Path | None, void: bool, cfg: Config
) -> tuple[Transcript | None, int]:
    """The judge's transcript, read by the adapter of the run's transcript format, and how
    many characters its prompt holds beyond the brief. A prompt without the whole brief is
    refused, except for a void, which only shows the agent never answered."""
    if transcript is None:
        return None, 0
    t = read_transcript(transcript, cfg)
    if void:
        return t, 0
    brief = normalise(read_text(rdir / f"brief_j{judge}.md"))
    prompt = normalise(t.prompt)
    if brief not in prompt:
        raise EvalError(
            f"this transcript's prompt does not contain brief j{judge} verbatim: "
            "paste the brief file's text"
        )
    return t, len(prompt) - len(brief)


@under_run_lock
def record_attempt(
    run: Path,
    rnd: int,
    judge: int,
    agent_id: str,
    raw: str,
    sealed: bool,
    source: str = "output",
    transcript: Path | None = None,
    void: bool = False,
    malformed: str = "",
    runner: Proof | None = None,
) -> Attempt:
    """record_reply, plus the runner's path: `runner` is its proof (source "runner"), and with
    `void` the process gave no reply. Only runner.judge_seat passes a proof. The caller holds
    the seat's lock (seat_lock); the run's lock is taken here, after it, for the run-wide
    checks (an agent id or a reply already used) and the store."""
    spec = load_frozen(run)
    cfg = run_config(run)
    if (runner is None) != (source != RUNNER):
        raise EvalError("a runner reply needs the runner's proof, and only a runner reply has one")
    if source != RUNNER:
        _refuse_in_a_runner_run(cfg)
    rdir = round_dir(run, rnd, sealed)
    keys = round_keys(rdir, rnd)
    meta = _judge_meta(keys, judge, rdir)
    if not agent_id.strip():
        raise EvalError("the agent id is required: it proves each judge is a fresh context")
    if agent_id in used_agent_ids(run):
        raise EvalError(
            f"agent {agent_id} already judged in this run: every brief needs a fresh judge"
        )
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: a late judge would be panel shopping")
    if void and runner is None:
        if transcript is None:
            raise EvalError("a void needs the agent's transcript, to show it never replied")
        if meta["nonce"] not in read_text(transcript):
            raise EvalError(f"this transcript never saw brief j{judge}: nothing to void")
    t, extra_chars = _read_proof(rdir, judge, transcript, void, cfg)
    prior = results_by_judge(rdir).get(judge, [])
    if void and t is not None:
        return _void(rdir, judge, agent_id, meta, t, prior)
    if void and runner is not None:
        return _runner_void(rdir, judge, agent_id, meta, raw, runner, prior)
    digest = hashlib.sha256(
        (f"malformed:{agent_id}" if malformed else raw).encode("utf-8")
    ).hexdigest()
    if digest in used_reply_hashes(run):
        raise EvalError("this exact reply is already recorded: one reply counts once")
    check_seat(judge, prior, source, cfg)
    dim_ids = [d["id"] for d in dims_for(spec, meta["persona"])]
    texts = {role: read_text(rdir / f"{role}.md") for role in set(meta["key"].values())}
    tools_used = t.tools if t is not None else ()
    result: Attempt
    if tools_used:
        result = {
            "ok": False,
            "errors": [
                f"the judge used a tool ({', '.join(tools_used)}): a judge gets no tools, so it "
                "could have read the run; brief a fresh judge on the same brief"
            ],
            "items": {},
        }
    else:
        try:
            if malformed:
                raise EvalError(malformed)
            result = check_output(extract_json(raw), meta["key"], texts, dim_ids, cfg)
        except (ValueError, RecursionError, EvalError) as exc:
            # ValueError: not JSON, or an integer past the conversion limit. Hostile input.
            reason = "nested too deeply" if isinstance(exc, RecursionError) else exc
            result = {"ok": False, "errors": [f"unparseable: {reason}"], "items": {}}
    if result["ok"]:
        calibrate(result, meta, spec)
    result.update(
        {
            "reply_sha256": digest,
            "judge": judge,
            "agent_id": agent_id,
            "persona": meta["persona"],
            "source": source,
            "transcript": str(transcript) if transcript else None,
            "prompt_extra_chars": extra_chars,
            "unreadable_lines": len(t.unreadable) if t is not None else 0,
        }
    )
    if runner is not None:
        result["runner"] = runner
    try:
        _store(rdir, judge, agent_id, raw, result)
    except RecursionError:  # parsed, but too deep to write back: never a reason to lose it
        result.update({"ok": False, "errors": ["unparseable: nested too deeply"], "items": {}})
        _store(rdir, judge, agent_id, raw, result)
    return result


def record_from(
    run: Path,
    rnd: int,
    judge: int,
    agent_id: str,
    sealed: bool,
    transcript: Path | None = None,
    output: str | None = None,
    void: bool = False,
) -> Attempt:
    """Record one judge as the `record` command does: from its transcript (or as a void on
    it), or from a pasted reply. The command line and the MCP server both call this.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        judge: The judge slot.
        agent_id: The id of the judge's context, new for every reply in the run.
        sealed: Whether the round is the sealed panel's.
        transcript: The judge's transcript, or None.
        output: The pasted reply, or None.
        void: Record the transcript's agent as an attempt that never replied.
    """
    _refuse_in_a_runner_run(run_config(run))
    if void and transcript is None:
        raise EvalError("a void needs the agent's transcript")
    if (transcript is None) == (output is None):
        raise EvalError("give the judge's transcript or its pasted reply, not both")
    if transcript is None:
        return record_reply(run, rnd, judge, agent_id, str(output), sealed, "output")
    t = read_transcript(transcript, run_config(run))
    found = t.agent_id
    if found and found != agent_id:
        raise EvalError(f"the agent id {agent_id} does not match the transcript's agent {found}")
    raw, source, malformed = "", ("void" if void else "transcript"), ""
    if not void:
        try:
            raw = reply_text(t)
        except MalformedReply as exc:
            malformed = str(exc)
        except EvalError as exc:
            # No reply, but tool calls: it cannot be voided, so it is recorded as rejected.
            if not t.tools:
                raise
            malformed = str(exc)
    return record_reply(run, rnd, judge, agent_id, raw, sealed, source, transcript, void, malformed)
