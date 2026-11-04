"""Quotes are checked by code, after normalising typography and ellipses."""

from __future__ import annotations

from sealed_panel.text import quote_found


class TestQuote:
    def test_smart_quotes_and_ellipsis(self) -> None:
        src = "He said \u201cwe ship on Friday\u201d \u2014 and we did, after the audit."
        assert quote_found('"we ship on Friday" - and we did', src)
        assert quote_found("we ship on Friday \u2026 after the audit", src)
        assert not quote_found("we ship on Monday", src)
        assert not quote_found("we", src), "a too-short quote proves nothing"

    def test_opening_smart_quote_matches_ascii(self) -> None:
        src = "He said \u201cwe ship on Friday\u201d and we did."
        assert quote_found('He said "we ship on Friday', src)

    def test_quote_ellipsis_forms_and_tiny_fragments(self) -> None:
        src = "We cut onboarding from nine days to two by replacing the manual review."
        for q in (
            "We cut onboarding [...] nine days to two",
            "We cut onboarding (...) nine days",
            "We cut onboarding .... nine days to two",
            "We cut onboarding. . . nine days",
        ):
            assert quote_found(q, src), q
        assert not quote_found("we ... cut ... on ... bo ... ar ... di", src)

    def test_a_quote_needs_twelve_characters(self) -> None:
        src = "Fraud fell after the queue went live."
        assert not quote_found("the queue", src)  # 9 chars: locates nothing
        assert quote_found("the queue went", src)

    def test_quote_with_an_ellipsis_in_the_source(self) -> None:
        assert quote_found("Wait... what happened next", "He said: Wait... what happened next?")
