"""End-to-end tests: main.py with the fixtures data.

Runs the real CLI as a subprocess, from parsing through FX,
matching, tax years and HTML reporting, and checks the
reports it writes. FX rates are pre-written into a
temporary cache in the providers' real file formats, using
the real published USD rates for the months the fixtures
need, so no test touches the network. The UK provider is
GBP, so it needs no rate.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
MAIN = ROOT / "main.py"

# Real published USD rates per £1 for the months the
# fixtures' USD trades fall in.
USD_RATES: dict[tuple[int, int], str] = {
    (2019, 1): "1.2643",
    (2020, 1): "1.3071",
    (2020, 3): "1.2952",
    (2020, 12): "1.3303",
    (2025, 1): "1.2707",
    (2025, 2): "1.2357",
    (2025, 3): "1.2585",
    (2025, 4): "1.2978",
    (2026, 1): "1.3329",
    (2026, 4): "1.3361",
    (2026, 5): "1.3502",
    (2026, 6): "1.3399",
}

_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
_LAST_DAY = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _seed_fx_cache(fx_dir: Path) -> None:
    for (year, month), rate in USD_RATES.items():
        month_label = f"{month:02d}"
        if year < 2021:
            period = (
                f"01/{_MONTHS[month - 1]}/{year} to "
                f"{_LAST_DAY[month - 1]}/{_MONTHS[month - 1]}/{year}"
            )
            _write(
                fx_dir / str(year) / month_label / f"hmrc-{year}-{month_label}.xml",
                '<?xml version="1.0"?>\n'
                f'<exchangeRateMonthList Period="{period}">'
                "<exchangeRate>"
                "<countryName>USA</countryName>"
                "<countryCode>US</countryCode>"
                "<currencyName>Dollar</currencyName>"
                "<currencyCode>USD</currencyCode>"
                f"<rateNew>{rate}</rateNew>"
                "</exchangeRate>"
                "</exchangeRateMonthList>\n",
            )
        else:
            _write(
                fx_dir
                / str(year)
                / month_label
                / f"trade-tariff-{year}-{month_label}.csv",
                "Country/Territories,Currency,Currency Code,"
                "Currency Units per £1,Start date,End date\n"
                f"United States,Dollar,USD,{rate},"
                f"01/{month_label}/{year},"
                f"{_LAST_DAY[month - 1]}/{month_label}/{year}\n",
            )


def _run_main(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MAIN), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


class EndToEndTests(unittest.TestCase):
    tmp: tempfile.TemporaryDirectory[str]
    output_dir: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.output_dir = Path(cls.tmp.name) / "output"
        _seed_fx_cache(cls.output_dir / "cache" / "fx")
        cls.result = _run_main(
            "--data-dir",
            str(FIXTURES),
            "--output-dir",
            str(cls.output_dir),
            "--clean-output",
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def _read(self, name: str) -> str:
        return (self.output_dir / name).read_text(encoding="utf-8")

    def test_run_exits_zero_and_writes_twenty_one_reports(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        # The one skipped fixture row is the Transfer row:
        # it is warned about while parsing, and the count
        # is repeated at the end, separated, so it is not
        # missed among the report lines.
        self.assertIn(
            "Warning: 1 input row skipped during parsing and in no report.",
            self.result.stderr,
        )
        expected = [
            f"{kind}-{label}.html"
            for kind in ("portfolio", "disposals", "dividends-interest")
            for label in (
                "2018-19",
                "2019-20",
                "2020-21",
                "2023-24",
                "2024-25",
                "2025-26",
                "2026-27_CURRENT",
            )
        ]
        self.assertEqual(
            sorted(p.name for p in self.output_dir.glob("*.html")), sorted(expected)
        )
        diagnostic = self.output_dir / "cache" / "fx" / "diagnostic.json"
        self.assertTrue(diagnostic.is_file())
        self.assertIn("1.2707", diagnostic.read_text(encoding="utf-8"))

    def test_per_year_total_gains(self) -> None:
        # Dividends must not change these: they are income,
        # not disposals, and income never touches a pool or
        # a gain.
        for label, total in [
            ("2019-20", "£100.00"),
            ("2020-21", "£0.00"),
            ("2024-25", "£209.22"),
            ("2025-26", "£163.60"),
            ("2026-27_CURRENT", "£147.09"),
        ]:
            with self.subTest(label=label):
                html = self._read(f"disposals-{label}.html")
                self.assertIn(f"<th>Total gain</th><td class='num'>{total}</td>", html)

    def test_per_year_dividends_and_interest(self) -> None:
        # Totals are sums of unrounded GBP amounts, shown to
        # the penny:
        # 2024/25 dividends are $10.50 + $20.00 at the Mar
        # 2025 rate of 1.2585, tax $3.00 at the same rate.
        # 2025/26 is a prior-year dividend of $18.00 and
        # $2.70 tax at the Jan 2026 rate of 1.3329, plus
        # $5.00 interest and $0.75 tax at the Apr 2025 rate
        # of 1.2978. 2026/27 is £8.00 + £12.00 dividends,
        # £1.80 tax, and £3.00 interest, all in GBP.
        cases = [
            ("2024-25", "£24.24", "£2.38", "£21.85", "£0.00", "£0.00", "£0.00"),
            ("2025-26", "£13.50", "£2.03", "£11.48", "£3.85", "£0.58", "£3.27"),
            (
                "2026-27_CURRENT",
                "£20.00",
                "£1.80",
                "£18.20",
                "£3.00",
                "£0.00",
                "£3.00",
            ),
        ]
        for label, div, div_tax, net_div, intr, intr_tax, net_intr in cases:
            with self.subTest(label=label):
                html = self._read(f"dividends-interest-{label}.html")
                for heading, total in [
                    ("Dividends received", div),
                    ("Foreign tax withheld on dividends", div_tax),
                    ("Net dividends received", net_div),
                    ("Interest received", intr),
                    ("Foreign tax withheld on interest", intr_tax),
                    ("Net interest received", net_intr),
                ]:
                    self.assertIn(
                        f"<th>{heading}</th><td class='num'>{total}</td>", html
                    )

    def test_income_table_shows_original_amount(self) -> None:
        # The gross dividend is $20.00 and the tax on it
        # -$3.00: the original amounts are shown alongside
        # the GBP ones, as in the events table. There is no
        # original price for income: a dividend has no price,
        # the amount is the value.
        html = self._read("dividends-interest-2024-25.html")
        income = html.split("<h2>Income in 2024/25</h2>", 1)[1]
        self.assertIn("<th>Original amount</th>", income)
        self.assertIn("20.00 USD", income)
        self.assertIn("-3.00 USD", income)

    def test_dividends_report_has_no_internal_file_names(self) -> None:
        html = self._read("dividends-interest-2025-26.html")
        self.assertIn("No local file names are included.", html)
        self.assertNotIn("test_data.csv", html)
        self.assertNotIn("transactions.csv", html)

    def test_current_year_is_marked_current(self) -> None:
        self.assertTrue((self.output_dir / "disposals-2026-27_CURRENT.html").is_file())
        self.assertFalse((self.output_dir / "disposals-2026-27.html").exists())
        self.assertTrue((self.output_dir / "disposals-2025-26.html").is_file())
        self.assertFalse((self.output_dir / "disposals-2025-26_CURRENT.html").exists())

    def test_holdings_at_5_april_2026(self) -> None:
        html = self._read("portfolio-2025-26.html")
        for row in [
            "<td>ACME</td><td class='num'>7</td><td class='num'>£284.86</td>",
            "<td>CROSS</td><td class='num'>10</td><td class='num'>£300.10</td>",
            "<td>EDGE</td><td class='num'>5</td><td class='num'>£75.02</td>",
            "<td>WIDG</td><td class='num'>20</td><td class='num'>£261.25</td>",
        ]:
            self.assertIn(row, html)

    def test_events_table_shows_fees(self) -> None:
        # The fees are the reason quantity times price is
        # not the amount: the broker's amount is net of
        # them. The 1 Feb 2025 ACME sell is 8 at $60.00
        # with $1.50 fees, so $478.50, not $480.00; in GBP
        # the fees are $1.50 at the Feb 2025 rate of
        # 1.2357, so £1.21.
        html = self._read("disposals-2024-25.html")
        events = html.split("<h2>Buy and sell events</h2>", 1)[1]
        self.assertIn("<th>Original fees</th>", events)
        self.assertIn("<th>GBP fees</th>", events)
        rows = events.split("<tr>")
        sell_row = next(row for row in rows if "<td>2025-02-01</td>" in row)
        self.assertIn("1.50 USD", sell_row)
        self.assertIn("£1.21", sell_row)

    def test_events_table_marks_tax_year_start(self) -> None:
        # The 2025/26 events table accumulates years of
        # history, so a separator marks where the year
        # itself starts: between the 5 April 2025 sell and
        # the 7 April 2025 buy.
        for name in ("disposals-2025-26.html", "portfolio-2025-26.html"):
            with self.subTest(report=name):
                html = self._read(name)
                events = html.split("<h2>Buy and sell events</h2>", 1)[1]
                self.assertIn("Tax year 2025/26 starts — 6 April 2025", events)
                self.assertLess(
                    events.index("<td>2025-04-05</td>"),
                    events.index("Tax year 2025/26 starts"),
                )
                self.assertLess(
                    events.index("Tax year 2025/26 starts"),
                    events.index("<td>2025-04-07</td>"),
                )
        # A year whose events all fall inside it has no
        # separator: the 2018/19 table starts in the year.
        html = self._read("portfolio-2018-19.html")
        self.assertNotIn("Tax year 2018/19 starts", html)

    def test_events_table_has_no_income(self) -> None:
        # Income lives in the dividends-and-interest report
        # only: dividends, interest and withholding tax do
        # not belong among the buys and sells. The 2025/26
        # events still hold the trades around them, so the
        # filter did not drop anything else.
        for name in ("disposals-2025-26.html", "portfolio-2025-26.html"):
            with self.subTest(report=name):
                html = self._read(name)
                self.assertIn("<h2>Buy and sell events</h2>", html)
                events = html.split("<h2>Buy and sell events</h2>", 1)[1]
                for action in (
                    "dividend",
                    "interest",
                    "withholding tax",
                ):
                    self.assertNotIn(f"<td>{action}</td>", events)
                self.assertIn("<td>2025-04-07</td>", events)
                self.assertIn("<td>2025-04-16</td>", events)

    def test_zero_fees_are_blank_not_zero(self) -> None:
        # The 5 April 2026 CROSS sell is 10 at $50.00 with
        # no fees, so $500.00: both fee cells are blank,
        # and no zero is printed anywhere in the row.
        html = self._read("disposals-2026-27_CURRENT.html")
        events = html.split("<h2>Buy and sell events</h2>", 1)[1]
        rows = events.split("<tr>")
        sell_row = next(
            row
            for row in rows
            if "<td>2026-04-05</td>" in row and "<td>CROSS</td>" in row
        )
        self.assertIn("500.00 USD", sell_row)
        self.assertIn("£374.22", sell_row)
        self.assertEqual(sell_row.count("<td class='num'></td>"), 2)
        self.assertNotIn("£0.00", sell_row)

    def test_future_buys_note_is_rendered(self) -> None:
        # The 2024/25 disposals are matched by buys after
        # the year end (7 and 15 April 2025), so both
        # reports carry the note explaining those buys are
        # audit-only. And the placeholder itself must never
        # leak into the output as literal text.
        for name in ("portfolio-2024-25.html", "disposals-2024-25.html"):
            with self.subTest(report=name):
                html = self._read(name)
                self.assertIn("Buys after year end", html)
        for path in sorted(self.output_dir.glob("*.html")):
            with self.subTest(report=path.name):
                self.assertNotIn("{future_note}", path.read_text(encoding="utf-8"))

    def test_events_table_has_no_stock_plan_activity(self) -> None:
        # The Stock Plan Activity rows are date-correction
        # records, not events: they are consumed by the
        # vesting join and must not leak into any report.
        for name in ("disposals-2025-26.html", "portfolio-2025-26.html"):
            with self.subTest(report=name):
                html = self._read(name)
                events = html.split("<h2>Buy and sell events</h2>", 1)[1]
                self.assertNotIn("<td>stock plan activity</td>", events)

    def test_disposals_report_has_no_internal_file_names(self) -> None:
        html = self._read("disposals-2025-26.html")
        self.assertIn("No local file names are included.", html)
        self.assertNotIn("test_data.csv", html)
        self.assertNotIn("transactions.csv", html)

    def test_missing_data_dir_exits_two(self) -> None:
        result = _run_main("--data-dir", str(Path(self.tmp.name) / "no-such-dir"))
        self.assertEqual(result.returncode, 2)

    def test_clean_output_removes_old_reports_but_not_cache(self) -> None:
        stale = self.output_dir / "portfolio-1999-00.html"
        stale.write_text("stale", encoding="utf-8")
        stale_income = self.output_dir / "dividends-interest-1999-00.html"
        stale_income.write_text("stale", encoding="utf-8")
        fx_file = (
            self.output_dir
            / "cache"
            / "fx"
            / "2025"
            / "01"
            / "trade-tariff-2025-01.csv"
        )
        result = _run_main(
            "--data-dir",
            str(FIXTURES),
            "--output-dir",
            str(self.output_dir),
            "--clean-output",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertFalse(stale_income.exists())
        self.assertTrue(fx_file.is_file())
