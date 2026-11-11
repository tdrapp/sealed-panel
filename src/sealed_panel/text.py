"""Text helpers: normalisation, quote checking, text hashes and injection flags."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .config import Config, resolve

# An ellipsis written as [...], (...), four dots or spaced dots counts as one ellipsis.
_ELLIPSIS_FORMS = r"[\[(]\s*\.{3,}\s*[\])]|\.(?:\s*\.){2,}"
_FRAGMENT_EDGES = "\"' .,;:"
# Hex characters kept from the hash that identifies a candidate text.
TEXT_HASH_CHARS = 16
# Typographic characters mapped to plain ones before texts are compared. Non-breaking spaces
# need no entry: NFKC already turns them into plain spaces, as it turns the ellipsis character
# into "...". These were the `[text]` defaults until 0.4.0, which made them fixed.
PUNCTUATION = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u00ab": '"',
    "\u00bb": '"',
    "\u2013": "-",
    "\u2014": "-",
}
# Characters removed before texts are compared: markdown emphasis and headings.
STRIP_CHARS = "*_`#>"
_STRIPPED = re.compile("[" + re.escape(STRIP_CHARS) + "]")


def normalise(text: str) -> str:
    """NFKC, the punctuation map, the stripped characters, collapsed whitespace, folded case.
    Every comparison of texts uses it: quote matching, the injection scan, the re-roll hash,
    the identity of two texts and the whole-brief check.

    Args:
        text: The text.
    """
    text = unicodedata.normalize("NFKC", text)
    for a, b in PUNCTUATION.items():
        text = text.replace(a, b)
    text = _STRIPPED.sub("", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def quote_parts(quote: str) -> list[str]:
    """The fragments of a quote, split on an ellipsis and normalised.

    Args:
        quote: The quote.
    """
    q = normalise(quote or "")
    q = re.sub(_ELLIPSIS_FORMS, "...", q)
    parts = [p.strip(_FRAGMENT_EDGES) for p in q.split("...")]
    return [p for p in parts if p]


def text_hash(text: str) -> str:
    """A short hash of a normalised text.

    Args:
        text: The text.
    """
    digest = hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()
    return digest[:TEXT_HASH_CHARS]


def quote_found(quote: str, source: str, cfg: Config | None = None) -> bool:
    """True when every fragment of the quote (split on an ellipsis) appears, in order.

    Args:
        quote: The judge's quote.
        source: The item it quotes.
        cfg: The run's configuration.
    """
    cfg = resolve(cfg)
    whole = "...".join(quote_parts(quote))
    if len(whole) >= cfg.min_quote_chars and whole in "...".join(quote_parts(source)):
        return True  # verbatim as a whole, ellipses in the source included
    parts = quote_parts(quote)
    if not parts or sum(len(p) for p in parts) < cfg.min_quote_chars:
        return False
    if any(len(p) < cfg.min_fragment_chars for p in parts):
        return False
    src = normalise(source)
    pos = 0
    for p in parts:
        i = src.find(p, pos)
        if i < 0:
            return False
        pos = i + len(p)
    return True


def injection_flags(text: str, cfg: Config | None = None) -> list[str]:
    """The injection patterns the text matches, once normalised.

    Args:
        text: The text.
        cfg: The run's configuration, which may add patterns.
    """
    cfg = resolve(cfg)
    low = normalise(text)
    return [p for p in cfg.injection_patterns if re.search(p, low)]
