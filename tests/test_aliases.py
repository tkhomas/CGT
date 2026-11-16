"""Tests for symbol aliases: aliases.py.

One holding under several spellings is one holding: an
ACMX sell must match an ACME buy, because the rename is not
a disposal. The tests check the file loading, the
validation that fails loud, the rewriting itself, and
the matching outcome with and without the alias.
"""

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from aliases import canonicalise_events, load_aliases
from matching import PoolInsufficientError, State
from models import Action, Currency, Event, ParsedEvent, Pricing
from processors import dispatch
from timeline import build_global_timeline


def _parsed(
    symbol: str,
    when: date,
    action: Action,
    quantity: int,
    price: int | Decimal,
    amount: int | Decimal,
) -> ParsedEvent:
    return ParsedEvent(
        date=when,
        action=action,
        symbol=symbol,
        description="",
        quantity=quantity,
        original_currency=Currency.GBP,
        original_price=Decimal(price),
        original_amount=Decimal(amount),
        original_fees=Decimal(0),
        provider="Test Broker",
        source_file="test.csv",
        row=1,
    )


def _to_event(parsed: ParsedEvent) -> Event:
    # GBP already, rate 1: the conversion is not what
    # this test is about.
    return Event(
        date=parsed.date,
        action=parsed.action,
        symbol=parsed.symbol,
        description=parsed.description,
        quantity=parsed.quantity,
        pricing=Pricing(
            original_currency=parsed.original_currency,
            original_price=parsed.original_price,
            original_amount=parsed.original_amount,
            original_fees=parsed.original_fees,
            original_units_per_gbp=Decimal(1),
            fx_source="GBP",
        ),
        provider=parsed.provider,
        source_file=parsed.source_file,
        row=parsed.row,
    )


def _run(parsed_events: list[ParsedEvent]) -> State:
    state = State()
    events = [_to_event(p) for p in parsed_events]
    for event in build_global_timeline(events):
        dispatch(event, state)
    state.matcher.finish(state.portfolio)
    return state


class LoadAliasesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write(self, text: str) -> Path:
        path = self.dir / "aliases.json"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_returns_no_aliases(self) -> None:
        self.assertEqual(load_aliases(self.dir / "aliases.json"), {})

    def test_loads_spelling_to_canonical_map(self) -> None:
        path = self._write(json.dumps({"ACME": ["ACME", "ACMX"], "GLOB": ["GLOB"]}))
        self.assertEqual(
            load_aliases(path),
            {"ACME": "ACME", "ACMX": "ACME", "GLOB": "GLOB"},
        )

    def test_malformed_file_raises(self) -> None:
        # Every way the file can be wrong is the same
        # outcome: a loud error, not a guess.
        cases = [
            ("not JSON", "{oops", ValueError),
            ("top level is a list", "[]", TypeError),
            ("value is not a list", '{"ACME": "ACMX"}', TypeError),
            ("entry is not a string", '{"ACME": [42]}', ValueError),
            ("empty entry", '{"ACME": [""]}', ValueError),
            ("empty canonical name", '{"": ["ACMX"]}', ValueError),
        ]
        for label, text, exception in cases:
            with self.subTest(label=label), self.assertRaises(exception):
                load_aliases(self._write(text))

    def test_spelling_under_two_keys_raises(self) -> None:
        # One spelling under two canonical names is a
        # contradiction: the file cannot mean both.
        path = self._write(json.dumps({"ACME": ["ACMX"], "GLOB": ["ACMX"]}))
        with self.assertRaises(ValueError):
            load_aliases(path)


class CanonicaliseTests(unittest.TestCase):
    def test_canonicalise_rewrites_and_leaves_others(self) -> None:
        events = [
            _parsed("ACMX", date(2025, 4, 16), Action.SELL, 5, 20, 100),
            _parsed("ACME", date(2025, 4, 16), Action.BUY, 5, 10, 50),
            _parsed("GLOB", date(2025, 4, 16), Action.BUY, 5, 10, 50),
        ]
        rewritten = canonicalise_events(events, {"ACMX": "ACME", "ACME": "ACME"})
        self.assertEqual(
            [event.symbol for event in rewritten], ["ACME", "ACME", "GLOB"]
        )
        # The parsed events are untouched.
        self.assertEqual(events[0].symbol, "ACMX")

    def test_canonicalise_without_aliases_leaves_everything(self) -> None:
        events = [_parsed("ACMX", date(2025, 4, 16), Action.SELL, 5, 20, 100)]
        self.assertEqual(
            [event.symbol for event in canonicalise_events(events, {})],
            ["ACMX"],
        )


class AliasMatchingTests(unittest.TestCase):
    def _events(self) -> list[ParsedEvent]:
        # An ACME buy and an ACMX sell on the same day: one
        # holding, so the sell must match the buy.
        return [
            _parsed("ACME", date(2025, 4, 16), Action.BUY, 5, 10, 50),
            _parsed("ACMX", date(2025, 4, 16), Action.SELL, 5, 20, 100),
        ]

    def test_acmx_sell_matches_acme_buy(self) -> None:
        parsed = canonicalise_events(self._events(), {"ACMX": "ACME"})
        state = _run(parsed)
        disposals = state.matcher.gains.disposals
        self.assertEqual(len(disposals), 1)
        self.assertEqual(disposals[0].symbol, "ACME")
        self.assertEqual(disposals[0].cost, Decimal(50))
        self.assertEqual(disposals[0].proceeds, Decimal(100))
        self.assertEqual(disposals[0].gain, Decimal(50))

    def test_acmx_sell_without_alias_screams(self) -> None:
        # Without the alias the sell has no pool behind
        # it: ACMX was never bought, ACME was never sold.
        with self.assertRaises(PoolInsufficientError):
            _run(self._events())


if __name__ == "__main__":
    unittest.main()
