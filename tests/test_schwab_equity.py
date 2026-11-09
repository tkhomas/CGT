"""Tests for the equity awards parser: parsers/schwab_equity.py.

One lapse is two physical rows: the first carries the
date, the action, the symbol and the quantity; the second,
with an empty date, carries the award details, including
the fair market value price. The amount is computed, the
tax columns are ignored. Made-up values throughout -- the
real file's META rows must never appear here.
"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path

from models import Action, Currency, ProviderConfig
from parsers.schwab_equity import parse_csv


def _provider(name="Test Equity", date_format="%m/%d/%Y", currency=Currency.USD):
    return ProviderConfig(
        name=name, date_format=date_format, currency=currency, parser="schwab_equity"
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "FeesAndCommissions",
    "DisbursementElection",
    "Amount",
    "AwardDate",
    "AwardId",
    "FairMarketValuePrice",
    "SalePrice",
    "SharesSoldWithheldForTaxes",
    "NetSharesDeposited",
    "Taxes",
]


def _lapse_pair(
    lapse_date="03/15/2021",
    action="Lapse",
    quantity="4",
    fees="",
    award_date="02/10/2020",
    award_id="887766",
    fmv="$41.25",
    withheld="1",
    net="3",
    taxes="$18.90",
):
    lapse = [
        lapse_date,
        action,
        "ACME",
        "Restricted Stock Lapse",
        quantity,
        fees,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ]
    detail = [
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        award_date,
        award_id,
        fmv,
        "",
        withheld,
        net,
        taxes,
    ]
    return ",".join(lapse) + "\n" + ",".join(detail) + "\n"


class EquityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _parse(self, text):
        path = _write(self.base / "lapses.csv", text)
        err = io.StringIO()
        with redirect_stderr(err):
            return parse_csv(path, _provider())

    def test_lapse_pair_is_a_buy_at_quantity_times_fmv(self):
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair())
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.action, Action.BUY)
        self.assertEqual(event.date, date(2021, 3, 15))
        self.assertEqual(event.symbol, "ACME")
        self.assertEqual(event.description, "Restricted Stock Lapse")
        self.assertEqual(event.quantity, 4)
        self.assertEqual(event.original_price, Decimal("41.25"))
        self.assertEqual(event.original_amount, Decimal("165.00"))
        self.assertEqual(event.original_fees, Decimal(0))
        self.assertEqual(event.provider, "Test Equity")
        self.assertEqual(event.source_file, "lapses.csv")
        self.assertEqual(event.row, 2)

    def test_tax_columns_are_ignored(self):
        # One share withheld for tax, tax paid -- the buy
        # is still the full quantity at the fair market
        # value: that tax is already paid via income tax.
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair())
        self.assertEqual(skipped, 0)
        self.assertEqual(events[0].quantity, 4)
        self.assertEqual(events[0].original_amount, Decimal("165.00"))

    def test_lapse_with_fees_includes_them_in_the_amount(self):
        # The amount is the money out: quantity times the
        # fair market value plus the fees. The matcher uses
        # the amount as it stands, so the fees belong in it
        # here, not added later.
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair(fees="$1.00"))
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].original_amount, Decimal("166.00"))
        self.assertEqual(events[0].original_fees, Decimal("1.00"))

    def test_wrapped_record_is_joined(self):
        # A record split across two lines -- the first
        # carrying the first eight columns, the second the
        # remaining seven -- joins into one lapse, complete
        # because the price is on the joined row.
        header = ",".join(HEADER) + "\n"
        first = "03/15/2021,Lapse,ACME,Restricted Stock Lapse,4,,,"
        second = "02/10/2020,887766,$41.25,,1,3,$18.90"
        events, skipped = self._parse(header + first + "\n" + second + "\n")
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].quantity, 4)
        self.assertEqual(events[0].original_amount, Decimal("165.00"))

    def test_joined_record_with_too_many_columns_raises(self):
        # A short line joined with a full detail line
        # overflows the column count: malformed input, and
        # the run must fail loudly rather than guess.
        header = ",".join(HEADER) + "\n"
        short = "03/15/2021,Lapse,ACME,Restricted Stock Lapse,4"
        detail = ",".join(
            [
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "02/10/2020",
                "887766",
                "$41.25",
                "",
                "1",
                "3",
                "$18.90",
            ]
        )
        path = _write(self.base / "lapses.csv", header + short + "\n" + detail + "\n")
        with self.assertRaises(ValueError):
            parse_csv(path, _provider())

    def test_short_record_at_end_of_file_raises(self):
        header = ",".join(HEADER) + "\n"
        short = "03/15/2021,Lapse,ACME,Restricted Stock Lapse,4"
        path = _write(self.base / "lapses.csv", header + short + "\n")
        with self.assertRaises(ValueError):
            parse_csv(path, _provider())

    def test_lapse_without_a_detail_row_is_skipped(self):
        header = ",".join(HEADER) + "\n"
        lapse = ",".join(
            [
                "03/15/2021",
                "Lapse",
                "ACME",
                "Restricted Stock Lapse",
                "4",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        events, skipped = self._parse(header + lapse + "\n")
        self.assertEqual(events, [])
        self.assertEqual(skipped, 1)

    def test_detail_row_without_a_lapse_is_skipped(self):
        header = ",".join(HEADER) + "\n"
        detail = ",".join(
            [
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "02/10/2020",
                "887766",
                "$41.25",
                "",
                "1",
                "3",
                "$18.90",
            ]
        )
        events, skipped = self._parse(header + detail + "\n")
        self.assertEqual(events, [])
        self.assertEqual(skipped, 1)

    def test_unrecognised_action_is_skipped(self):
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair(action="Sale"))
        self.assertEqual(events, [])
        self.assertEqual(skipped, 1)

    def test_lapse_without_a_price_is_skipped(self):
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair(fmv=""))
        self.assertEqual(events, [])
        self.assertEqual(skipped, 1)

    def test_fractional_zero_and_unparseable_quantities_are_skipped(self):
        header = ",".join(HEADER) + "\n"
        for label, quantity in [
            ("fractional", "2.5"),
            ("zero", "0"),
            ("not a number", "ten"),
        ]:
            with self.subTest(label=label):
                events, skipped = self._parse(header + _lapse_pair(quantity=quantity))
                self.assertEqual(events, [])
                self.assertEqual(skipped, 1)

    def test_quantity_with_thousands_separator_parses(self):
        # Schwab writes large quantities with a thousands
        # separator: "1,000" is a thousand units.
        header = ",".join(HEADER) + "\n"
        events, skipped = self._parse(header + _lapse_pair(quantity='"1,000"'))
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].quantity, 1000)
        self.assertEqual(events[0].original_amount, Decimal("41250.00"))


if __name__ == "__main__":
    unittest.main()
