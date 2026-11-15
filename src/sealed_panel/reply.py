"""Read a judge's reply: extract its JSON and validate it against the brief's key."""

from __future__ import annotations

import json
import re
from fractions import Fraction
from typing import Any

from .errors import EvalError
from .settings import Config, resolve
from .text import normalise, quote_found

SCORE_MIN, SCORE_MAX = 1, 5


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
            quotes.append(normalise(quote))
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
