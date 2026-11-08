"""Tests for event parsing: parsing.py and provider discovery
in providers.py.

One file, one test per piece of real behaviour, not one test
per edge case. Cases with the same outcome are grouped in a
single test with subTest, so each case is still reported
separately. Tests go through the public functions and check
what comes back -- events, skipped counts, returned configs
-- not warning text or internal helpers.

Made-up values are used where a value must never become real:
"frobnicate" can never be an action, "ZZZ" can never be a
currency.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from decimal import Decimal
from pathlib import Path

from models import Action, Currency, ProviderConfig
from parsing import parse_action, parse_csv, parse_date, parse_money
from providers import find_inputs, load_provider_config


def _provider(name="Test Broker", date_format="%m/%d/%Y", currency=Currency.USD):
    return ProviderConfig(name=name, date_format=date_format, currency=currency)


def _write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = ",".join(header) + "\n" + "\n".join(",".join(r) for r in rows) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


HEADER = [
    "Date",
    "Action",
    "Symbol",
    "Description",
    "Quantity",
    "Price",
    "Amount",
    "Fees & Comm",
]


class ParseHelpersTests(unittest.TestCase):
    def test_parse_date_uses_the_provider_format(self):
        # The same calendar day written in two provider
        # formats must give the same date; the format is
        # per-provider config, never assumed.
        cases = [
            ("01/15/2025", "%m/%d/%Y", date(2025, 1, 15)),
            ("15/01/2025", "%d/%m/%Y", date(2025, 1, 15)),
        ]
        for text, fmt, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_date(text, fmt), expected)

    def test_parse_date_takes_first_date_when_two_dates_are_given(self):
        # Schwab can show "08/18/2026 as of 08/17/2026":
        # the first date is the transaction date, the
        # second is the date it is reported as of.
        cases = [
            ("08/18/2026 as of 08/17/2026", "%m/%d/%Y", date(2026, 8, 18)),
            ("18/08/2026 as of 17/08/2026", "%d/%m/%Y", date(2026, 8, 18)),
            ("08/18/2026", "%m/%d/%Y", date(2026, 8, 18)),
        ]
        for text, fmt, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_date(text, fmt), expected)

    def test_parse_money_strips_symbols_commas_and_space(self):
        # Money cleanup only: it never decides the currency,
        # that comes from the provider config.
        cases = [
            (None, Decimal(0)),
            ("", Decimal(0)),
            ("$1,234.56", Decimal("1234.56")),
            ("£100.00", Decimal("100.00")),
            ("€50", Decimal(50)),
            ("¥1,000", Decimal(1000)),
            ("  $12.34  ", Decimal("12.34")),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_money(text), expected)

    def test_parse_action_recognises_buy_and_sell(self):
        cases = [
            ("buy", Action.BUY),
            ("BUY", Action.BUY),
            (" Buy ", Action.BUY),
            ("sell", Action.SELL),
            ("Sell", Action.SELL),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_action(text, "test.csv", 2), expected)
        # An action that can never be added to the enum:
        # unrecognised actions return None, they are not
        # silently treated as a buy or a sell.
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertIsNone(parse_action("frobnicate", "test.csv", 2))
            self.assertIsNone(parse_action(None, "test.csv", 2))
            self.assertIsNone(parse_action("", "test.csv", 2))

    def test_parse_action_maps_income_names_to_canonical_actions(self):
        # Schwab names several things that are one thing
        # for UK tax: both dividend names map to Dividend,
        # both interest names map to Interest, and all
        # three tax names map to Withholding Tax, the only
        # tax action -- a tax row does not say what it is
        # tax on, and reporting decides that from the
        # dividend row with the same symbol on the same
        # date.
        cases = [
            ("Qualified Dividend", Action.DIVIDEND),
            ("qualified dividend", Action.DIVIDEND),
            ("Pr Yr Non-Qual Div", Action.DIVIDEND),
            ("NRA Tax Adj", Action.WITHHOLDING_TAX),
            ("Foreign Tax Paid", Action.WITHHOLDING_TAX),
            ("Pr Yr NRA Tax", Action.WITHHOLDING_TAX),
            ("Credit Interest", Action.INTEREST),
            ("Bond Interest", Action.INTEREST),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_action(text, "test.csv", 2), expected)

    def test_parse_action_recognises_ignored_actions(self):
        # Ignored actions are recognised, not unrecognised:
        # they must not warn and must not be skipped.
        cases = [
            ("Stock Plan Activity", Action.STOCK_PLAN_ACTIVITY),
            ("stock plan activity", Action.STOCK_PLAN_ACTIVITY),
            ("Wire Sent", Action.WIRE_SENT),
            ("wire sent", Action.WIRE_SENT),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_action(text, "test.csv", 2), expected)


class ParseCsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_csv_reads_buy_and_sell_rows(self):
        path = _write_csv(
            self.dir / "transactions.csv",
            HEADER,
            [
                [
                    "01/15/2025",
                    "Buy",
                    " ACME ",
                    "Buy ACME",
                    "10",
                    "$50.00",
                    "$500.00",
                    "$1.00",
                ],
                [
                    "02/01/2025",
                    "Sell",
                    "ACME",
                    "Sell ACME",
                    "8",
                    "$60.00",
                    "$480.00",
                    "$1.50",
                ],
            ],
        )
        events, skipped = parse_csv(path, _provider())
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 2)

        buy, sell = events
        self.assertEqual(buy.date, date(2025, 1, 15))
        self.assertEqual(buy.action, Action.BUY)
        self.assertEqual(buy.symbol, "ACME")
        self.assertEqual(buy.description, "Buy ACME")
        self.assertEqual(buy.quantity, 10)
        self.assertEqual(buy.original_currency, Currency.USD)
        self.assertEqual(buy.original_price, Decimal("50.00"))
        self.assertEqual(buy.original_amount, Decimal("500.00"))
        self.assertEqual(buy.original_fees, Decimal("1.00"))
        self.assertEqual(buy.provider, "Test Broker")
        self.assertEqual(buy.source_file, "transactions.csv")
        self.assertEqual(buy.row, 2)

        self.assertEqual(sell.date, date(2025, 2, 1))
        self.assertEqual(sell.action, Action.SELL)
        self.assertEqual(sell.quantity, 8)
        self.assertEqual(sell.original_price, Decimal("60.00"))
        self.assertEqual(sell.original_fees, Decimal("1.50"))
        self.assertEqual(sell.row, 3)

    def test_parse_csv_skips_bad_rows_but_keeps_good_ones(self):
        # Fractional quantity, non-positive quantity, and an
        # unrecognised action are all the same outcome -- the
        # row is skipped, the rest of the file still parses.
        # The good row keeps its real file row number (5),
        # not a renumbered event index.
        path = _write_csv(
            self.dir / "transactions.csv",
            HEADER,
            [
                [
                    "01/15/2025",
                    "Buy",
                    "ACME",
                    "fractional",
                    "2.5",
                    "$50.00",
                    "$125.00",
                    "$0.00",
                ],
                ["01/16/2025", "Buy", "ACME", "zero", "0", "$50.00", "$0.00", "$0.00"],
                [
                    "01/17/2025",
                    "Frobnicate",
                    "ACME",
                    "unknown",
                    "1",
                    "$50.00",
                    "$50.00",
                    "$0.00",
                ],
                [
                    "01/18/2025",
                    "Buy",
                    "ACME",
                    "good",
                    "10",
                    "$50.00",
                    "$500.00",
                    "$0.00",
                ],
            ],
        )
        err = io.StringIO()
        with redirect_stderr(err):
            events, skipped = parse_csv(path, _provider())
        self.assertEqual(skipped, 3)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].quantity, 10)
        self.assertEqual(events[0].row, 5)

    def test_parse_csv_amount_inconsistency_does_not_skip(self):
        # Quantity x price not matching amount is a warning,
        # not a skip: the row is still an event. Only the
        # outcome is tested here, not the warning text.
        path = _write_csv(
            self.dir / "transactions.csv",
            HEADER,
            [
                [
                    "01/15/2025",
                    "Buy",
                    "ACME",
                    "mismatch",
                    "10",
                    "$50.00",
                    "$999.00",
                    "$0.00",
                ]
            ],
        )
        err = io.StringIO()
        with redirect_stderr(err):
            events, skipped = parse_csv(path, _provider())
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].original_amount, Decimal("999.00"))

    def test_parse_csv_without_a_fees_column_gives_zero_fees(self):
        header = [
            "Date",
            "Action",
            "Symbol",
            "Description",
            "Quantity",
            "Price",
            "Amount",
        ]
        path = _write_csv(
            self.dir / "transactions.csv",
            header,
            [
                [
                    "01/15/2025",
                    "Buy",
                    "ACME",
                    "no fees column",
                    "10",
                    "$50.00",
                    "$500.00",
                ]
            ],
        )
        events, skipped = parse_csv(path, _provider())
        self.assertEqual(skipped, 0)
        self.assertEqual(events[0].original_fees, Decimal(0))

    def test_parse_csv_ignores_stock_plan_activity_and_wire_sent(self):
        # Both rows shaped like a real Schwab export: the
        # dates, actions and empty fields are as given,
        # the ticker and description are made up. Both are
        # dropped at parsing, silently: the stock plan row
        # has a quantity but no price, the wire row has no
        # quantity at all, and neither is a CGT event. A
        # normal Buy in the same file still parses as usual.
        path = _write_csv(
            self.dir / "transactions.csv",
            HEADER,
            [
                [
                    "08/18/2026 as of 08/17/2026",
                    "Stock Plan Activity",
                    "ACME",
                    "ACME HOLDINGS INC CLASS A",
                    "1",
                    "",
                    "",
                    "",
                ],
                [
                    "09/04/2026",
                    "Wire Sent",
                    "",
                    "WIRED FUNDS DISBURSED",
                    "",
                    "",
                    "",
                    "-$100.00",
                ],
                [
                    "09/05/2026",
                    "Buy",
                    "ACME",
                    "Buy ACME",
                    "10",
                    "$50.00",
                    "$500.00",
                    "$0.00",
                ],
            ],
        )
        err = io.StringIO()
        with redirect_stderr(err):
            events, skipped = parse_csv(path, _provider())
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 1)

        buy = events[0]
        self.assertEqual(buy.action, Action.BUY)
        self.assertEqual(buy.date, date(2026, 9, 5))
        self.assertEqual(buy.symbol, "ACME")

    def test_parse_csv_reads_income_rows(self):
        # Rows shaped like a Schwab export, with made-up
        # tickers, dates and values throughout. Income rows
        # carry no quantity and no price: the Amount is the
        # value, positive for a dividend or interest,
        # negative for tax already paid. A prior-year
        # dividend is still just a dividend here; the tax
        # year it lands in is decided by its payment date,
        # later. A normal Buy in the same file still
        # parses as usual.
        path = _write_csv(
            self.dir / "transactions.csv",
            HEADER,
            [
                [
                    "09/11/2026",
                    "Qualified Dividend",
                    "ACME",
                    "ACME HOLDINGS INC",
                    "",
                    "",
                    "$40.00",
                    "",
                ],
                [
                    "09/11/2026",
                    "Foreign Tax Paid",
                    "ACME",
                    "ACME HOLDINGS INC",
                    "",
                    "",
                    "-$6.00",
                    "",
                ],
                [
                    "09/30/2026",
                    "Credit Interest",
                    "",
                    "BROKER INT 09/01-09/30",
                    "",
                    "",
                    "$2.50",
                    "",
                ],
                [
                    "06/20/2026",
                    "Bond Interest",
                    "99999FAKE1",
                    "FAKE TREASURY NOTE 4% DUE 12/15/27",
                    "",
                    "",
                    "$1.25",
                    "",
                ],
                [
                    "01/20/2026",
                    "Pr Yr Non-Qual Div",
                    "GLOB",
                    "GLOB HOLDINGS INC",
                    "",
                    "",
                    "$18.00",
                    "",
                ],
                [
                    "01/20/2026",
                    "Pr Yr NRA Tax",
                    "GLOB",
                    "GLOB HOLDINGS INC",
                    "",
                    "",
                    "-$2.70",
                    "",
                ],
                [
                    "09/05/2026",
                    "Buy",
                    "ACME",
                    "Buy ACME",
                    "10",
                    "$50.00",
                    "$500.00",
                    "$0.00",
                ],
            ],
        )
        err = io.StringIO()
        with redirect_stderr(err):
            events, skipped = parse_csv(path, _provider())
        self.assertEqual(err.getvalue(), "")
        self.assertEqual(skipped, 0)
        self.assertEqual(len(events), 7)

        dividend, tax, credit, bond, prior, prior_tax, buy = events
        self.assertEqual(dividend.action, Action.DIVIDEND)
        self.assertEqual(dividend.date, date(2026, 9, 11))
        self.assertEqual(dividend.symbol, "ACME")
        self.assertEqual(dividend.quantity, 0)
        self.assertEqual(dividend.original_amount, Decimal("40.00"))
        self.assertEqual(tax.action, Action.WITHHOLDING_TAX)
        self.assertEqual(tax.original_amount, Decimal("-6.00"))
        self.assertEqual(credit.action, Action.INTEREST)
        self.assertEqual(credit.symbol, "")
        self.assertEqual(credit.original_amount, Decimal("2.50"))
        self.assertEqual(bond.action, Action.INTEREST)
        self.assertEqual(bond.symbol, "99999FAKE1")
        self.assertEqual(bond.original_amount, Decimal("1.25"))
        self.assertEqual(prior.action, Action.DIVIDEND)
        self.assertEqual(prior.date, date(2026, 1, 20))
        self.assertEqual(prior.original_amount, Decimal("18.00"))
        self.assertEqual(prior_tax.action, Action.WITHHOLDING_TAX)
        self.assertEqual(prior_tax.original_amount, Decimal("-2.70"))
        self.assertEqual(buy.action, Action.BUY)
        self.assertEqual(buy.symbol, "ACME")


class ProviderDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _provider_dir(self, name, config):
        provider_dir = self.base / "input" / name
        provider_dir.mkdir(parents=True, exist_ok=True)
        if config is not None:
            (provider_dir / "config.json").write_text(
                json.dumps(config), encoding="utf-8"
            )
        return provider_dir

    def test_load_provider_config(self):
        valid = {"name": "  Broker A  ", "date_format": "%m/%d/%Y", "currency": "USD"}
        cases = [
            ("valid, name stripped", valid, ("Broker A", "%m/%d/%Y", Currency.USD)),
            ("missing file", None, None),
            ("not an object", ["not", "a", "dict"], None),
            ("missing name", {**valid, "name": ""}, None),
            (
                "missing date_format",
                {k: v for k, v in valid.items() if k != "date_format"},
                None,
            ),
            ("unknown currency", {**valid, "currency": "ZZZ"}, None),
        ]
        for label, config, expected in cases:
            with self.subTest(label=label):
                provider_dir = self._provider_dir(f"p-{label}", config)
                # A bad config prints a warning on stderr;
                # the warning text is not what this test checks.
                with redirect_stderr(io.StringIO()):
                    result = load_provider_config(provider_dir)
                if expected is None:
                    self.assertIsNone(result)
                else:
                    self.assertEqual(
                        result,
                        ProviderConfig(
                            name=expected[0],
                            date_format=expected[1],
                            currency=expected[2],
                        ),
                    )
        # Invalid JSON is the same outcome as a missing file:
        # no config, no crash.
        provider_dir = self._provider_dir("p-bad-json", None)
        (provider_dir / "config.json").write_text("{not json", encoding="utf-8")
        with redirect_stderr(io.StringIO()):
            result = load_provider_config(provider_dir)
        self.assertIsNone(result)

    def test_find_inputs_collects_csvs_and_skips_problems(self):
        # One provider with a CSV at the top and one in a
        # subfolder, a stray file directly under input/, a
        # non-CSV file, a duplicate provider name, and a
        # provider with no config. The outcome is the list
        # of CSVs from the first provider only.
        (self.base / "input").mkdir(parents=True, exist_ok=True)
        (self.base / "input" / "stray.txt").write_text("stray", encoding="utf-8")

        broker_a = self._provider_dir(
            "broker-a",
            {"name": "Broker A", "date_format": "%m/%d/%Y", "currency": "USD"},
        )
        _write_csv(broker_a / "transactions.csv", HEADER, [])
        _write_csv(broker_a / "nested" / "more.csv", HEADER, [])
        (broker_a / "notes.txt").write_text("notes", encoding="utf-8")

        broker_b = self._provider_dir(
            "broker-b",
            {"name": "Broker A", "date_format": "%m/%d/%Y", "currency": "USD"},
        )
        _write_csv(broker_b / "transactions.csv", HEADER, [])

        broker_c = self._provider_dir("broker-c", None)
        _write_csv(broker_c / "transactions.csv", HEADER, [])

        err = io.StringIO()
        with redirect_stderr(err):
            inputs = find_inputs(self.base)
        self.assertIsNotNone(inputs)
        self.assertEqual(
            [(p.path.name, p.provider.name) for p in inputs],
            [("more.csv", "Broker A"), ("transactions.csv", "Broker A")],
        )
        # config.json itself is never returned as an input.
        self.assertNotIn("config.json", [p.path.name for p in inputs])

    def test_find_inputs_returns_none_when_no_inputs(self):
        cases = [
            ("no input dir at all", None),
            ("input dir, no providers", "empty"),
        ]
        for label, setup in cases:
            with self.subTest(label=label):
                base = self.base / f"case-{label}"
                base.mkdir(parents=True, exist_ok=True)
                if setup == "empty":
                    (base / "input").mkdir(parents=True, exist_ok=True)
                err = io.StringIO()
                with redirect_stderr(err):
                    self.assertIsNone(find_inputs(base))


if __name__ == "__main__":
    unittest.main()
