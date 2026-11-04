"""Text helpers: normalisation, quote checking, text hashes and injection flags."""

from __future__ import annotations

import functools
import hashlib
import re
import unicodedata

from .config import Config, default_data, resolve

# An ellipsis written as [...], (...), four dots or spaced dots counts as one ellipsis.
_ELLIPSIS_FORMS = r"[\[(]\s*\.{3,}\s*[\])]|\.(?:\s*\.){2,}"
_FRAGMENT_EDGES = "\"' .,;:"
# Hex characters kept from the hash that identifies a candidate text.
TEXT_HASH_CHARS = 16


def normalise_with(text: str, punctuation: dict[str, str], strip_chars: str) -> str:
    """NFKC, the punctuation map, the stripped characters, collapsed whitespace, folded case."""
    text = unicodedata.normalize("NFKC", text)
    for a, b in punctuation.items():
        text = text.replace(a, b)
    if strip_chars:
        text = re.sub("[" + re.escape(strip_chars) + "]", "", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalise(text: str, cfg: Config | None = None) -> str:
    """The quote check's normalisation, with the run's `[text]` settings."""
    cfg = resolve(cfg)
    return normalise_with(text, cfg.punctuation, cfg.strip_chars)


@functools.cache
def _builtin_text() -> tuple[tuple[tuple[str, str], ...], str]:
    text = default_data()["text"]
    return tuple(text["punctuation"].items()), text["strip_chars"]


def fixed_normalise(text: str) -> str:
    """The built-in `[text]` settings, whatever the configuration says. Every check but quote
    matching uses it: the injection scan, the re-roll hash, the identity of two texts and the
    whole-brief check. An override can change which quotes are found, nothing else."""
    punctuation, strip_chars = _builtin_text()
    return normalise_with(text, dict(punctuation), strip_chars)


def quote_parts(quote: str, cfg: Config | None = None) -> list[str]:
    q = normalise(quote or "", cfg)
    q = re.sub(_ELLIPSIS_FORMS, "...", q)
    parts = [p.strip(_FRAGMENT_EDGES) for p in q.split("...")]
    return [p for p in parts if p]


def text_hash(text: str) -> str:
    digest = hashlib.sha256(fixed_normalise(text).encode("utf-8")).hexdigest()
    return digest[:TEXT_HASH_CHARS]


def quote_found(quote: str, source: str, cfg: Config | None = None) -> bool:
    """True when every fragment of the quote (split on an ellipsis) appears, in order."""
    cfg = resolve(cfg)
    whole = "...".join(quote_parts(quote, cfg))
    if len(whole) >= cfg.min_quote_chars and whole in "...".join(quote_parts(source, cfg)):
        return True  # verbatim as a whole, ellipses in the source included
    parts = quote_parts(quote, cfg)
    if not parts or sum(len(p) for p in parts) < cfg.min_quote_chars:
        return False
    if any(len(p) < cfg.min_fragment_chars for p in parts):
        return False
    src = normalise(source, cfg)
    pos = 0
    for p in parts:
        i = src.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def injection_flags(text: str, cfg: Config | None = None) -> list[str]:
    cfg = resolve(cfg)
    low = fixed_normalise(text)
    return [p for p in cfg.injection_patterns if re.search(p, low)]
