"""Unit tests for the HTML reporting helpers.

The end-to-end tests cover the reports as a whole; these
pin the rendering of the events table itself, including
a case the fixtures never produce in a report: a row
whose quantity, price and fees are all zero renders
those cells blank rather than as zeros.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from models import Action, Currency, Event, Pricing
from reporting.html import _events_rows


def _event(
    *,
    action: Action,
    quantity: int,
    price: str,
    amount: str,
    fees: str,
) -> Event:
    return Event(
        date=date(2025, 3, 10),
        action=action,
        symbol="ACME",
        description="ACME CORP CLASS A",
        quantity=quantity,
        pricing=Pricing(
            original_currency=Currency.USD,
            original_price=Decimal(price),
            original_amount=Decimal(amount),
            original_fees=Decimal(fees),
            original_units_per_gbp=Decimal("1.2585"),
            fx_source="trade-tariff:2025-03",
        ),
        provider="Super trader 007",
        source_file="test_data.csv",
        row=1,
    )


class EventsRowsTests(unittest.TestCase):
    def test_zero_quantity_price_and_fees_are_blank(self) -> None:
        # An income-style row: quantity and price are zero,
        # the amount is the income itself. The zeros are
        # blank; the amount is shown, in both currencies.
        rows = _events_rows(
            [
                _event(
                    action=Action.DIVIDEND,
                    quantity=0,
                    price="0",
                    amount="20.00",
                    fees="0",
                )
            ],
            internal_source=False,
        )
        self.assertIn("20.00 USD", rows)
        self.assertIn("£15.89", rows)
        # Five blank cells: quantity, original price,
        # original fees, GBP price, GBP fees.
        self.assertEqual(rows.count("<td class='num'></td>"), 5)
        self.assertNotIn("£0.00", rows)

    def test_zero_fees_are_blank_on_a_trade(self) -> None:
        rows = _events_rows(
            [
                _event(
                    action=Action.SELL,
                    quantity=10,
                    price="50.00",
                    amount="500.00",
                    fees="0",
                )
            ],
            internal_source=False,
        )
        self.assertIn("500.00 USD", rows)
        self.assertEqual(rows.count("<td class='num'></td>"), 2)
        self.assertNotIn("£0.00", rows)
