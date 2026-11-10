"""Record one judge's reply (or one instrument reading) for a round, and validate it.

A reply comes either from a file the judge's text was saved to, or from a transcript: a JSONL
file whose assistant entries carry text blocks. The field names of a transcript entry are set
in the [transcript] section of the configuration.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import statistics
from collections.abc import Iterator
from fractions import Fraction
from pathlib import Path
from typing import Any

from .config import Config, resolve
from .errors import EvalError, MalformedReply
from .ledger import results_by_judge, used_agent_ids, used_reply_hashes
from .spec import dims_for, load_frozen, run_config
from .store import dump_json, load_json, locked, read_text, round_dir, under_run_lock
from .text import fixed_normalise, quote_found

SCORE_MIN, SCORE_MAX = 1, 5
# Characters searched for the agent id, from the start of a transcript.
AGENT_ID_SCAN_CHARS = 20_000
# Widths used in a recorded reply's file name.
AGENT_TAG_CHARS = 12
SAFE_NAME_CHARS = 40
# Void attempts allowed per judge slot and round. A void never fills a seat, so voids have
# their own cap and do not count toward max_replacements.
MAX_VOIDS = 3
# The source of a reply read by the judge runner: sealed-panel wrote the brief and read the reply.
RUNNER = "runner"


def transcript_agent_id(transcript: Path, cfg: Config | None = None) -> str | None:
    """The agent id the transcript itself carries (None when it carries none)."""
    t = resolve(cfg).transcript
    pattern = '"' + re.escape(str(t["agent_id_key"])) + r'"\s*:\s*"([^"]+)"'
    m = re.search(pattern, read_text(transcript)[:AGENT_ID_SCAN_CHARS])
    return m.group(1) if m else None


def _lines(transcript: Path) -> list[tuple[int, dict[str, Any] | None]]:
    """(line number, entry) for every non-empty line; None for a line that is not a JSON object.
    Lines end at a newline only: a JSON string may hold other line separators."""
    out: list[tuple[int, dict[str, Any] | None]] = []
    for n, line in enumerate(read_text(transcript).split("\n"), start=1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except (ValueError, RecursionError):
            e = None
        out.append((n, e if isinstance(e, dict) else None))
    return out


def _entries(transcript: Path) -> list[dict[str, Any]]:
    return [e for _, e in _lines(transcript) if e is not None]


def unreadable_lines(transcript: Path) -> list[int]:
    """The numbers of the transcript's non-empty lines that are not a JSON object. Reading
    skips them; `record` counts them in its result."""
    return [n for n, e in _lines(transcript) if e is None]


def _block_text(content: Any, t: dict[str, Any]) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(b.get(t["text_key"], ""))
            for b in content
            if isinstance(b, dict) and b.get(t["block_type_key"]) == t["text_block_type"]
        )
    return ""


def _content(e: dict[str, Any], t: dict[str, Any]) -> Any:
    message = e.get(t["message_key"])
    return message.get(t["content_key"]) if isinstance(message, dict) else None


def transcript_prompt(transcript: Path, cfg: Config | None = None) -> str:
    """The text of the first user entry: the prompt the agent was launched with."""
    t = resolve(cfg).transcript
    for e in _entries(transcript):
        if e.get(t["role_key"]) == t["user_role"]:
            return _block_text(_content(e, t), t)
    return ""


def assistant_texts(transcript: Path, cfg: Config | None = None) -> list[str]:
    """Every non-empty assistant text of a transcript, in order."""
    t = resolve(cfg).transcript
    texts = []
    for e in _entries(transcript):
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        if isinstance(content, list):
            text = _block_text(content, t)
            if text.strip():
                texts.append(text)
    return texts


def handback_messages(
    transcript: Path, cfg: Config | None = None, include_refused: bool = False
) -> list[str | None]:
    """The messages the agent returned through the configured handback tool, in order; None
    for a call whose message is not one non-empty string (never coerced into a reply). Some
    agent runtimes return a subagent's answer as a tool call rather than as a final text.

    A call whose tool result came back as an error was refused by the runtime and never reached
    the caller. By default such calls are left out, so the list holds what was delivered. With
    include_refused, they are kept: last_reply uses that list when nothing was delivered at all,
    and a void uses it because a refused verdict still shows the judge replied. An empty
    handback_tool setting means no tool is read."""
    t = resolve(cfg).transcript
    tool = t["handback_tool"]
    if not tool:
        return []
    entries = _entries(transcript)
    refused = set()
    for e in entries:
        if e.get(t["role_key"]) != t["user_role"]:
            continue
        content = _content(e, t)
        if isinstance(content, list):
            for b in content:
                if (
                    isinstance(b, dict)
                    and b.get(t["block_type_key"]) == t["tool_result_type"]
                    and b.get(t["tool_error_key"]) is True
                ):
                    refused.add(b.get(t["tool_result_id_key"]))
    messages: list[str | None] = []
    for e in entries:
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        if not isinstance(content, list):
            continue
        for b in content:
            if (
                not isinstance(b, dict)
                or b.get(t["block_type_key"]) != t["tool_block_type"]
                or b.get(t["tool_name_key"]) != tool
            ):
                continue
            call_id = b.get(t["tool_id_key"])
            if not include_refused and call_id is not None and call_id in refused:
                continue  # refused by the runtime: never delivered
            inp = b.get(t["tool_input_key"])
            msg = inp.get(t["tool_message_key"]) if isinstance(inp, dict) else None
            messages.append(msg if isinstance(msg, str) and msg.strip() else None)
    return messages


def tool_calls(transcript: Path, cfg: Config | None = None) -> list[tuple[str, str]]:
    """(block type, name) of every tool the agent called in its transcript, in order."""
    t = resolve(cfg).transcript
    names = []
    for e in _entries(transcript):
        if e.get(t["role_key"]) != t["assistant_role"]:
            continue
        content = _content(e, t)
        for b in content if isinstance(content, list) else []:
            # Any type ending like the tool block: a server's tool call is a tool call too.
            if isinstance(b, dict) and str(b.get(t["block_type_key"])).endswith(
                str(t["tool_block_type"])
            ):
                names.append((str(b.get(t["block_type_key"])), str(b.get(t["tool_name_key"]))))
    return names


def judge_tools(transcript: Path, cfg: Config | None = None) -> list[str]:
    """The tools the judge called, sorted, the configured handback tool excepted (the way some
    runtimes return a reply). A judge gets no tools: a call to any other tool means it could
    have read the run directory, labels and roles included."""
    t = resolve(cfg).transcript
    exempt = (str(t["tool_block_type"]), str(t["handback_tool"]))
    return sorted({name for kind, name in tool_calls(transcript, cfg) if (kind, name) != exempt})


def last_reply(transcript: Path, cfg: Config | None = None) -> str:
    """A transcript's reply: its last delivered handback message, since that is what its
    caller received; else its last non-empty assistant text; else, when nothing was delivered
    at all, its last refused handback, which the judge still wrote. A malformed last handback,
    delivered or refused, raises MalformedReply; no reply at all is an error, never a guess.

    A last line that is not a JSON object raises MalformedReply: a transcript cut off while
    being written may have lost the final reply, and the text before it is not that reply.

    Args:
        transcript: The judge's JSONL transcript.
        cfg: The run's configuration.
    """
    lines = _lines(transcript)
    if lines and lines[-1][1] is None:
        raise MalformedReply(
            f"the last line of {transcript} (line {lines[-1][0]}) is not a readable entry: the "
            "transcript may have been cut off before its final reply"
        )
    handed = handback_messages(transcript, cfg)
    if handed:
        last = handed[-1]
        if last is None:
            raise MalformedReply(f"malformed handback in {transcript}: its message is not a text")
        return last
    texts = assistant_texts(transcript, cfg)
    if texts:
        return texts[-1]
    refused = handback_messages(transcript, cfg, include_refused=True)
    if refused:
        if refused[-1] is None:
            raise MalformedReply(
                f"malformed refused handback in {transcript}: its message is not a text"
            )
        return refused[-1]
    raise EvalError(f"no assistant reply found in {transcript}")


FENCE_OPENING = re.compile(r"```(?:json)?[ \t\r\n]*\{")


def extract_json(text: str) -> Any:
    """The JSON object in a judge's output: inside its fence when it wrote one, else from the
    first brace to the last. Linear in the size of the text: a reply is hostile input."""
    start, end = text.find("{"), text.rfind("}")
    opening = FENCE_OPENING.search(text)
    if opening:
        closing = text.rfind("```")
        last = text.rfind("}", 0, closing) if closing >= opening.end() else -1
        if last >= opening.end() - 1 and not text[last + 1 : closing].strip():
            start, end = opening.end() - 1, last
    if start < 0 or end <= start:
        raise EvalError("no JSON object in the judge output")
    return json.loads(text[start : end + 1])


def check_output(
    out: Any,
    key: dict[str, str],
    texts: dict[str, str],
    dim_ids: list[str],
    cfg: Config | None = None,
) -> dict[str, Any]:
    """Validate one judge's JSON against its label key. Scores are kept per ROLE, never label.

    Args:
        out: The judge's parsed JSON.
        key: The brief's label key: label to role.
        texts: The text of each role, for the quote check.
        dim_ids: The dimensions the persona is scored on.
        cfg: The configuration; the packaged defaults when None.
    """
    cfg = resolve(cfg)
    caps = cfg.severity_caps
    errors: list[str] = []
    parsed: dict[str, Any] = {}
    items = out.get("items") if isinstance(out, dict) else None
    if not isinstance(items, dict):
        return {"ok": False, "errors": ["'items' object missing"], "items": {}}
    quotes_total = quotes_bad = 0
    for label, role in key.items():
        it = items.get(label)
        if not isinstance(it, dict):
            errors.append(f"item {label}: missing")
            continue
        raw_dims = it.get("dimensions")
        dims: dict[str, Any] = raw_dims if isinstance(raw_dims, dict) else {}
        scores: dict[str, int] = {}
        raw_scores: dict[str, int] = {}
        severities: dict[str, str] = {}
        unverified: list[str] = []
        capped: list[str] = []
        quotes: list[str] = []
        quoted: dict[str, str] = {}
        for did in dim_ids:
            d = dims.get(did)
            if not isinstance(d, dict):
                errors.append(f"item {label}: dimension {did} missing")
                continue
            s, sev = d.get("score"), d.get("severity")
            if isinstance(s, bool) or not isinstance(s, int) or not SCORE_MIN <= s <= SCORE_MAX:
                errors.append(f"item {label}/{did}: score must be an integer 1-5")
                continue
            if sev not in cfg.severities:
                errors.append(f"item {label}/{did}: severity must be one of {cfg.severities}")
                continue
            if not str(d.get("reasoning", "")).strip():
                errors.append(f"item {label}/{did}: reasoning missing")
                continue
            raw_scores[did] = s
            quotes_total += 1
            quote = str(d.get("quote", ""))
            quotes.append(fixed_normalise(quote))
            quoted[did] = quote
            if not quote_found(quote, texts[role], cfg):
                quotes_bad += 1
                unverified.append(did)
                continue
            if s > caps[sev]:
                s = caps[sev]
                capped.append(did)
            scores[did], severities[did] = s, sev
        raw_wp = it.get("weakest_point")
        wp: dict[str, Any] = raw_wp if isinstance(raw_wp, dict) else {}
        wdim = wp.get("dimension")
        parsed[role] = {
            "scores": scores,
            "raw_scores": raw_scores,
            "severity": severities,
            "unverified": unverified,
            "capped": capped,
            "one_quote": len(quotes) >= 2 and len(set(quotes)) == 1,
            "quotes": quoted,
            "retell": it.get("retell", ""),
            "weakest_point": {
                "text": wp.get("text", ""),
                "dimension": wdim,
                "quote": wp.get("quote", ""),
                "severity": severities.get(wdim, "unknown") if isinstance(wdim, str) else "unknown",
            },
            "reasoning": {
                did: (dims.get(did) or {}).get("reasoning", "")
                for did in dim_ids
                if isinstance(dims.get(did), dict)
            },
        }
    if quotes_total and Fraction(quotes_bad, quotes_total) > cfg.max_bad_share:
        errors.append(f"{quotes_bad}/{quotes_total} quotes not found in the items")
    return {"ok": not errors, "errors": errors, "items": parsed}


def safe_name(s: str) -> str:
    """A string fit for a file name, cut short.

    Args:
        s: The string.
    """
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:SAFE_NAME_CHARS]


def _calibrate(result: dict[str, Any], meta: dict[str, Any], spec: dict[str, Any]) -> None:
    """Discard a judge whose control is too high or whose known-good is too low."""
    ctrl = result["items"].get("control", {}).get("raw_scores", {})
    if not ctrl:
        result["ok"] = False
        result["errors"].append("the control has no verified score")
    else:
        mean_ctrl = statistics.mean(ctrl.values())
        result["control_mean"] = round(mean_ctrl, 2)
        if mean_ctrl > spec["control_max"]:
            result["ok"] = False
            result["errors"].append(
                f"control scored {mean_ctrl:.2f} > {spec['control_max']}: this judge does "
                "not discriminate; brief a replacement on the same brief file"
            )
    good = result["items"].get("known_good", {}).get("raw_scores", {})
    has_good = "known_good" in meta["key"].values()
    if (
        result["ok"]
        and has_good
        and (not good or statistics.mean(good.values()) < spec["good_min"])
    ):
        result["ok"] = False
        result["errors"].append(
            f"known-good scored below {spec['good_min']}: this judge is too harsh or the "
            "rubric does not fit; brief a replacement"
        )


def _store(rdir: Path, judge: int, agent_id: str, raw: str, result: dict[str, Any]) -> None:
    tag = hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:AGENT_TAG_CHARS]
    dump_json(
        rdir / f"out_j{judge}_{safe_name(agent_id)}_{tag}.json", {"raw": raw, "result": result}
    )


def _void(
    rdir: Path,
    judge: int,
    agent_id: str,
    meta: dict[str, Any],
    transcript: Path,
    prior: list[dict[str, Any]],
    cfg: Config,
) -> dict[str, Any]:
    """An agent that received a brief and stopped without a reply: recorded as void so the
    round can be aggregated. A void clears the unrecorded-agent block but never fills a seat.
    Refused when the agent got the whole brief and left any reply: that reply is recorded."""
    brief = fixed_normalise(read_text(rdir / f"brief_j{judge}.md"))
    pasted = brief in fixed_normalise(transcript_prompt(transcript, cfg))
    # Fails closed: any tool call counts as a reply, whichever its name, because a runtime may
    # deliver the answer through a tool this run does not know. Only silence can be voided.
    replied = bool(tool_calls(transcript, cfg)) or bool(assistant_texts(transcript, cfg))
    if pasted and replied:
        raise EvalError(
            "this judge got the brief and replied: record its reply, it cannot be voided"
        )
    check_void_cap(judge, prior)
    result: dict[str, Any] = {
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
        "transcript": str(transcript),
        "reply_sha256": hashlib.sha256(f"void:{agent_id}".encode()).hexdigest(),
    }
    _store(rdir, judge, agent_id, "", result)
    return result


def check_void_cap(judge: int, prior: list[dict[str, Any]]) -> None:
    """Refuse a further void on a slot that already has MAX_VOIDS of them."""
    voids = [r for r in prior if r.get("void")]
    if len(voids) >= MAX_VOIDS:
        raise EvalError(
            f"judge {judge} already has {len(voids)} voided attempts: find why its agents stop "
            "before voiding more"
        )


def check_seat(judge: int, prior: list[dict[str, Any]], source: str, cfg: Config) -> None:
    """Refuse a reply the slot cannot take: a second valid one, a pasted one where the brief
    may have been seen, or one past the replacement cap."""
    if any(r["ok"] for r in prior):
        raise EvalError(
            f"judge {judge} already has a valid reply in this round: "
            "a second one would be a re-roll"
        )
    if source == "output" and any(r.get("void") and r.get("replied") for r in prior):
        raise EvalError(
            f"judge {judge} already had a reply voided (brief not verbatim): its next reply must "
            "come from a transcript holding the pasted brief, never pasted by hand"
        )
    real = [r for r in prior if not r.get("void")]
    if len(real) > cfg.max_replacements:
        raise EvalError(
            f"judge {judge} was rejected {len(real)} times: stop and fix the brief or the rubric"
        )


def _runner_void(
    rdir: Path,
    judge: int,
    agent_id: str,
    meta: dict[str, Any],
    raw: str,
    proof: dict[str, Any],
    prior: list[dict[str, Any]],
) -> dict[str, Any]:
    """A judge process that failed or timed out: the brief was delivered, no reply came back.
    Recorded as void, under the void cap; whatever it wrote is kept for a human, never read."""
    check_void_cap(judge, prior)
    result: dict[str, Any] = {
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


def round_keys(rdir: Path, rnd: int) -> dict[str, Any]:
    """The round's keys.json, refused when the round is not briefed or the file is damaged.

    Args:
        rdir: The round directory.
        rnd: The round number, from 1.
    """
    path = rdir / "keys.json"
    if not path.exists():
        raise EvalError(f"round {rnd} is not briefed ({path} is missing)")
    keys = load_json(path)
    if not isinstance(keys, dict) or not isinstance(keys.get("judges"), list):
        raise EvalError(f"{path} is damaged: no list of judges")
    return keys


def _judge_meta(keys: dict[str, Any], judge: int, rdir: Path) -> dict[str, Any]:
    meta = next(
        (j for j in keys["judges"] if isinstance(j, dict) and j.get("judge") == judge), None
    )
    if meta is None:
        raise EvalError(f"no judge {judge} in {rdir}/keys.json")
    return meta


@contextlib.contextmanager
def seat_lock(rdir: Path, judge: int) -> Iterator[None]:
    """Hold a seat of a round (an flock on `seat_j<J>.lock` beside its brief) while the block
    runs: every call that records an attempt on the seat holds it from the seat check to the
    stored attempt, so two calls never both pass the check. Raises SeatTaken when another call
    holds it.

    Args:
        rdir: The round directory.
        judge: The judge slot.
    """
    busy = f"seat {judge} is held by another call (a judge running or a reply being recorded)"
    with locked(rdir / f"seat_j{judge}.lock", busy):
        yield


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
) -> dict[str, Any]:
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
    rdir = round_dir(run, rnd, sealed)
    _judge_meta(round_keys(rdir, rnd), judge, rdir)  # a seat that exists, before its lock file
    with seat_lock(rdir, judge):
        return _record_reply(
            run, rnd, judge, agent_id, raw, sealed, source, transcript, void, malformed
        )


@under_run_lock
def _record_reply(
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
    runner: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
        raise EvalError("--agent-id is required: it proves each judge is a fresh context")
    if agent_id in used_agent_ids(run):
        raise EvalError(
            f"agent {agent_id} already judged in this run: every brief needs a fresh judge"
        )
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: a late judge would be panel shopping")
    extra_chars = 0
    if void and runner is None:
        if transcript is None:
            raise EvalError("--void needs the agent's --transcript, to show it never replied")
        if not meta.get("nonce"):
            raise EvalError("this run's briefs carry no brief id: a void cannot be checked")
        if meta["nonce"] not in read_text(transcript):
            raise EvalError(f"this transcript never saw brief j{judge}: nothing to void")
    elif transcript is not None:
        brief = fixed_normalise(read_text(rdir / f"brief_j{judge}.md"))
        prompt = fixed_normalise(transcript_prompt(transcript, cfg))
        if brief not in prompt:
            raise EvalError(
                f"this transcript's prompt does not contain brief j{judge} verbatim: "
                "paste the brief file's text"
            )
        extra_chars = len(prompt) - len(brief)
    prior = results_by_judge(rdir).get(judge, [])
    if void and transcript is not None:
        return _void(rdir, judge, agent_id, meta, transcript, prior, cfg)
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
    tools_used = judge_tools(transcript, cfg) if transcript is not None else []
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
        _calibrate(result, meta, spec)
    result.update(
        {
            "reply_sha256": digest,
            "judge": judge,
            "agent_id": agent_id,
            "persona": meta["persona"],
            "source": source,
            "transcript": str(transcript) if transcript else None,
            "prompt_extra_chars": extra_chars,
            "unreadable_lines": len(unreadable_lines(transcript)) if transcript else 0,
        }
    )
    if runner is not None:
        result["runner"] = runner
    try:
        _store(rdir, judge, agent_id, raw, result)
    except RecursionError:  # parsed, but too deep to write back: never a reason to lose it
        result.update(ok=False, errors=["unparseable: nested too deeply"], items={})
        _store(rdir, judge, agent_id, raw, result)
    return result


def outcome_line(r: dict[str, Any]) -> str:
    """What `record` and `judge` print for one recorded attempt."""
    if r.get("void"):
        return f"VOID judge {r['judge']}: " + (
            "; ".join(r["errors"][:1])
            if r.get("source") == RUNNER
            else "recorded as an attempt without a reply"
        )
    return (
        ("VALID" if r["ok"] else "REJECTED")
        + f" judge {r['judge']}"
        + ("" if r["ok"] else ": " + "; ".join(r["errors"][:3]))
    )


def record_from(
    run: Path,
    rnd: int,
    judge: int,
    agent_id: str,
    sealed: bool,
    transcript: Path | None = None,
    output: str | None = None,
    void: bool = False,
) -> dict[str, Any]:
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
        raise EvalError("--void needs --transcript")
    if (transcript is None) == (output is None):
        raise EvalError("give the judge's transcript or its pasted reply, not both")
    if transcript is None:
        return record_reply(run, rnd, judge, agent_id, str(output), sealed, "output")
    frozen = run_config(run)
    found = transcript_agent_id(transcript, frozen)
    if found and found != agent_id:
        raise EvalError(f"--agent-id {agent_id} does not match the transcript's agent {found}")
    raw, source, malformed = "", ("void" if void else "transcript"), ""
    if not void:
        try:
            raw = last_reply(transcript, frozen)
        except MalformedReply as exc:
            malformed = str(exc)
        except EvalError as exc:
            # No reply, but tool calls: it cannot be voided, so it is recorded as rejected.
            if not judge_tools(transcript, frozen):
                raise
            malformed = str(exc)
    return record_reply(run, rnd, judge, agent_id, raw, sealed, source, transcript, void, malformed)


def record_measurement(
    run: Path, rnd: int, mid: str, value: str, passed: bool | None, sealed: bool
) -> bool:
    """Record one instrument reading. A numeric threshold decides pass or fail.

    Args:
        run: The run directory.
        rnd: The round number, from 1.
        mid: The measured criterion's id.
        value: The reading.
        passed: Pass or fail, for a criterion without a numeric threshold; else None.
        sealed: Whether the round is the sealed panel's.
    """
    spec = load_frozen(run)
    run_config(run)
    m = next((x for x in spec.get("measured") or [] if x["id"] == mid), None)
    if m is None:
        raise EvalError(f"{mid} is not a measured criterion of this rubric")
    if "max" in m or "min" in m:
        if passed is not None:
            raise EvalError(f"{mid} has a numeric threshold: the script decides pass or fail")
        try:
            num = float(value)
        except ValueError as exc:
            raise EvalError(f"{mid} needs a number, got {value!r}") from exc
        passed = ("max" not in m or num <= m["max"]) and ("min" not in m or num >= m["min"])
    elif passed is None:
        raise EvalError(f"{mid} has no numeric threshold: say --pass or --fail")
    rdir = round_dir(run, rnd, sealed)
    if not (rdir / "keys.json").exists():
        raise EvalError("brief the round before measuring it")
    if (rdir / "result.json").is_file():
        raise EvalError("this round is already aggregated: measure before aggregating")
    path = rdir / "measured.json"
    rows = load_json(path) if path.exists() else []
    if any(r["id"] == mid for r in rows):
        raise EvalError(f"{mid} is already measured for this round: a measurement is taken once")
    rows = [*rows, {"id": mid, "value": value, "pass": passed}]
    dump_json(path, rows)
    return passed
