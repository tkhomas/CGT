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
# fixtures' USD trades fall in. HMRC months are 2015-2020,
# Trade Tariff months are 2021 onwards.
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

    def test_run_exits_zero_and_writes_fourteen_reports(self) -> None:
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        expected = [
            f"{kind}-{label}.html"
            for kind in ("portfolio", "disposals")
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
        # Verified by hand from the fixture data on 2026-11-03.
        # Dividends must not change these: they are disposals
        # only, and the fixture Dividend rows are skipped.
        for label, total in [
            ("2019-20", "£100.00"),
            ("2020-21", "£0.00"),
            ("2024-25", "£209.33"),
            ("2025-26", "£163.71"),
            ("2026-27_CURRENT", "£147.35"),
        ]:
            with self.subTest(label=label):
                html = self._read(f"disposals-{label}.html")
                self.assertIn(f"<th>Total gain</th><td class='num'>{total}</td>", html)

    def test_current_year_is_marked_current(self) -> None:
        self.assertTrue((self.output_dir / "disposals-2026-27_CURRENT.html").is_file())
        self.assertFalse((self.output_dir / "disposals-2026-27.html").exists())
        self.assertTrue((self.output_dir / "disposals-2025-26.html").is_file())
        self.assertFalse((self.output_dir / "disposals-2025-26_CURRENT.html").exists())

    def test_holdings_at_5_april_2026(self) -> None:
        html = self._read("portfolio-2025-26.html")
        for row in [
            "<td>ACME</td><td class='num'>8</td><td class='num'>£325.14</td>",
            "<td>CROSS</td><td class='num'>10</td><td class='num'>£300.10</td>",
            "<td>EDGE</td><td class='num'>5</td><td class='num'>£75.02</td>",
            "<td>WIDG</td><td class='num'>20</td><td class='num'>£261.25</td>",
        ]:
            self.assertIn(row, html)

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
        self.assertTrue(fx_file.is_file())
