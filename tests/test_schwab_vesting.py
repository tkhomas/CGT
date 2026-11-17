"""Tests for the vesting date correction: parsers/schwab_vesting.py.

The equity file dates a vest on the scheduled day, the
personal file's Stock Plan Activity row says when the
shares actually vested. The join turns each lapse into
the buy it is, with the date corrected to the record's
"as of" date, and drops the record itself. Made-up values
throughout.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from models import Action, Currency, ParsedEvent
from parsers.schwab_vesting import correct_vest_dates


def _event(
    *,
    action: Action,
    day: date,
    symbol: str = "ACME",
    quantity: int = 1,
    source_file: str = "test_data.csv",
    row: int = 2,
) -> ParsedEvent:
    return ParsedEvent(
        date=day,
        action=action,
        symbol=symbol,
        description="test",
        quantity=quantity,
        original_currency=Currency.USD,
        original_price=Decimal("50.00"),
        original_amount=Decimal("50.00"),
        original_fees=Decimal(0),
        provider="Test",
        source_file=source_file,
        row=row,
    )


def _lapse(day: date, quantity: int = 1, **kwargs) -> ParsedEvent:
    return _event(action=Action.LAPSE, day=day, quantity=quantity, **kwargs)


def _vesting_record(day: date, quantity: int = 1, **kwargs) -> ParsedEvent:
    return _event(
        action=Action.STOCK_PLAN_ACTIVITY, day=day, quantity=quantity, **kwargs
    )


def _buy(day: date, quantity: int = 1, **kwargs) -> ParsedEvent:
    return _event(action=Action.BUY, day=day, quantity=quantity, **kwargs)


class CorrectVestDatesTests(unittest.TestCase):
    def test_weekend_vest_is_corrected_to_the_as_of_date(self):
        # The scheduled vest day was a Sunday, so the
        # shares vested on Monday: the lapse becomes a buy
        # with the Monday date, and the record itself is
        # gone.
        events = correct_vest_dates(
            [
                _lapse(date(2022, 5, 15), quantity=5),
                _vesting_record(date(2022, 5, 16), quantity=5),
            ]
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, Action.BUY)
        self.assertEqual(events[0].date, date(2022, 5, 16))
        self.assertEqual(events[0].quantity, 5)
        self.assertEqual(events[0].original_price, Decimal("50.00"))

    def test_two_vests_in_one_month_pair_in_order(self):
        # Two vests in April, different quantities: the
        # earlier record pairs with the earlier lapse, and
        # each lapse becomes a buy with its own record's
        # date. The order of the returned list is the input
        # order, so the buys are found by quantity, not
        # position.
        events = correct_vest_dates(
            [
                _lapse(date(2025, 4, 16), quantity=3),
                _lapse(date(2025, 4, 15), quantity=6),
                _vesting_record(date(2025, 4, 17), quantity=3),
                _vesting_record(date(2025, 4, 16), quantity=6),
            ]
        )
        self.assertEqual(len(events), 2)
        by_quantity = {event.quantity: event for event in events}
        self.assertEqual(by_quantity[6].action, Action.BUY)
        self.assertEqual(by_quantity[6].date, date(2025, 4, 16))
        self.assertEqual(by_quantity[3].action, Action.BUY)
        self.assertEqual(by_quantity[3].date, date(2025, 4, 17))

    def test_regular_buy_in_the_same_month_is_untouched(self):
        # A genuine buy of the same stock in the same month
        # is not a lapse: it keeps its date, and it does
        # not steal the vesting record's pairing. The lapse
        # becomes a buy with the record's date.
        events = correct_vest_dates(
            [
                _buy(date(2025, 4, 10), quantity=6),
                _lapse(date(2025, 4, 15), quantity=6),
                _vesting_record(date(2025, 4, 16), quantity=6),
            ]
        )
        self.assertEqual(len(events), 2)
        regular, vest = events
        self.assertEqual(regular.action, Action.BUY)
        self.assertEqual(regular.date, date(2025, 4, 10))
        self.assertEqual(vest.action, Action.BUY)
        self.assertEqual(vest.date, date(2025, 4, 16))

    def test_lapse_with_no_records_becomes_a_buy_as_it_is(self):
        # No Stock Plan Activity rows at all means there is
        # no correction source: the equity dates stand, as
        # before, and the lapse still becomes a buy. This
        # is not a mismatch -- a mismatch is a record that
        # disagrees, or a lapse left unmatched when other
        # records exist.
        events = correct_vest_dates(
            [
                _buy(date(2025, 4, 10), quantity=6),
                _lapse(date(2025, 4, 15), quantity=6),
            ]
        )
        self.assertEqual(len(events), 2)
        regular, vest = events
        self.assertEqual(regular.action, Action.BUY)
        self.assertEqual(regular.date, date(2025, 4, 10))
        self.assertEqual(vest.action, Action.BUY)
        self.assertEqual(vest.date, date(2025, 4, 15))

    def test_quantity_mismatch_stops(self):
        with self.assertRaises(ValueError):
            correct_vest_dates(
                [
                    _lapse(date(2022, 5, 15), quantity=5),
                    _vesting_record(date(2022, 5, 16), quantity=4),
                ]
            )

    def test_vesting_record_with_no_lapse_stops(self):
        with self.assertRaises(ValueError):
            correct_vest_dates(
                [
                    _vesting_record(date(2022, 5, 16), quantity=5),
                ]
            )

    def test_lapse_missing_its_record_stops(self):
        # Two lapses in a month, one record: the second
        # lapse has no partner, so the run stops rather
        # than guessing its date.
        with self.assertRaises(ValueError):
            correct_vest_dates(
                [
                    _lapse(date(2025, 4, 15), quantity=6),
                    _lapse(date(2025, 4, 16), quantity=3),
                    _vesting_record(date(2025, 4, 16), quantity=6),
                ]
            )

    def test_extra_record_in_a_month_with_two_lapses_stops(self):
        with self.assertRaises(ValueError):
            correct_vest_dates(
                [
                    _lapse(date(2025, 4, 15), quantity=6),
                    _lapse(date(2025, 4, 16), quantity=3),
                    _vesting_record(date(2025, 4, 16), quantity=6),
                    _vesting_record(date(2025, 4, 17), quantity=3),
                    _vesting_record(date(2025, 4, 18), quantity=2),
                ]
            )
