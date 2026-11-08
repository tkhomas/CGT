"""Tests for per-tax-year income sums: reporting/tax_year.py.

Income events are built in GBP directly (rate 1), so FX does
not muddy the sums. Tests check the four totals for a tax
year -- dividends, tax on dividends, interest, tax on
interest -- and that a tax row is counted as dividend tax
only when a dividend row exists with the same symbol on
the same date.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from models import Action, Currency, Event, Pricing
from reporting.tax_year import IncomeTotals, income_for_year, tax_year_of


def _income_event(
    symbol: str,
    when: date,
    action: Action,
    amount: int | Decimal,
) -> Event:
    # Income rows carry no quantity and no price: the
    # amount is the value itself, positive for a dividend
    # or interest, negative for tax already withheld.
    return Event(
        date=when,
        action=action,
        symbol=symbol,
        description="",
        quantity=0,
        pricing=Pricing(
            original_currency=Currency.GBP,
            original_price=Decimal(0),
            original_amount=Decimal(amount),
            original_fees=Decimal(0),
            original_units_per_gbp=Decimal(1),
            fx_source="GBP",
        ),
        provider="Test Broker",
        source_file="test.csv",
        row=1,
    )


class IncomeForYearTests(unittest.TestCase):
    def test_sums_dividends_interest_and_tax_per_year(self):
        # Made-up tickers, dates and values throughout. A
        # prior-year dividend paid in January 2026 is still
        # 2025/26, because the tax year is the year of the
        # payment date, not the year it relates to.
        income = [
            _income_event("ACME", date(2026, 9, 11), Action.DIVIDEND, 40),
            _income_event("ACME", date(2026, 9, 11), Action.WITHHOLDING_TAX, -6),
            _income_event("", date(2026, 9, 30), Action.INTEREST, "2.50"),
            _income_event("", date(2026, 9, 30), Action.WITHHOLDING_TAX, "-0.50"),
            _income_event("GLOB", date(2026, 1, 20), Action.DIVIDEND, 18),
            _income_event("GLOB", date(2026, 1, 20), Action.WITHHOLDING_TAX, "-2.70"),
        ]
        self.assertEqual(
            income_for_year(income, tax_year_of(date(2026, 9, 11))),
            IncomeTotals(
                dividends=Decimal(40),
                dividend_tax=Decimal(6),
                interest=Decimal("2.50"),
                interest_tax=Decimal("0.50"),
            ),
        )
        self.assertEqual(
            income_for_year(income, tax_year_of(date(2026, 1, 20))),
            IncomeTotals(
                dividends=Decimal(18),
                dividend_tax=Decimal("2.70"),
                interest=Decimal(0),
                interest_tax=Decimal(0),
            ),
        )

    def test_withholding_is_interest_tax_without_a_matching_dividend(self):
        # A tax row counts as dividend tax only when a
        # dividend row exists with the same symbol on the
        # same date. A tax row for a stock with no dividend
        # that day, and a tax row with no symbol on a day
        # another stock paid a dividend, are both interest
        # tax: no dividend row matches them.
        income = [
            _income_event("ACME", date(2026, 10, 10), Action.DIVIDEND, 10),
            _income_event("WIDG", date(2026, 10, 10), Action.WITHHOLDING_TAX, -1),
            _income_event("", date(2026, 10, 10), Action.WITHHOLDING_TAX, "-0.25"),
        ]
        self.assertEqual(
            income_for_year(income, tax_year_of(date(2026, 10, 10))),
            IncomeTotals(
                dividends=Decimal(10),
                dividend_tax=Decimal(0),
                interest=Decimal(0),
                interest_tax=Decimal("1.25"),
            ),
        )


if __name__ == "__main__":
    unittest.main()
