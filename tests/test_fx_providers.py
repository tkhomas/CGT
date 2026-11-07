"""Tests for the FX providers in fx/providers.py.

Each provider is tested against a fake file in the format it
expects, written to a temporary directory. No test here touches
the network: the fake file is pre-written at the provider's
raw_path, so get_rate reads it instead of downloading.

Rates in these files are invented (1.5, 2.0, ...) to make it
obvious they are fake, except where a real published rate is
used as a known value.
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from decimal import Decimal
from pathlib import Path

from fx.providers import (
    HmrcXmlProvider,
    TradeTariffProvider,
    UserProvidedRatesProvider,
    UserRateNotFoundError,
)
from models import Currency


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TradeTariffProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx_dir = Path(self.tmp.name)
        self.provider = TradeTariffProvider(self.fx_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_csv(self, year=2025, month=1, rows=None):
        if rows is None:
            rows = [
                "United States,Dollar,USD,1.2707,01/01/2025,31/01/2025",
                "Eurozone,Euro,EUR,1.2106,01/01/2025,31/01/2025",
                "Japan,Yen,JPY,195.2761,01/01/2025,31/01/2025",
            ]
        text = (
            "Country/Territories,Currency,Currency Code,"
            "Currency Units per £1,Start date,End date\n" + "\n".join(rows) + "\n"
        )
        return _write(self.provider.raw_path(year, month), text)

    def test_reads_real_format_rates(self):
        self._write_csv()
        self.assertEqual(
            self.provider.get_rate(2025, 1, Currency.USD), Decimal("1.2707")
        )
        self.assertEqual(
            self.provider.get_rate(2025, 1, Currency.EUR), Decimal("1.2106")
        )
        self.assertEqual(
            self.provider.get_rate(2025, 1, Currency.JPY), Decimal("195.2761")
        )

    def test_missing_currency_raises(self):
        self._write_csv()
        # CHF is already a known currency (in the enum), just
        # not in this month's file. This tests absence from
        # the file, not absence from the enum, so adding a
        # currency to the enum cannot break it.
        with self.assertRaises(ValueError) as caught:
            self.provider.get_rate(2025, 1, Currency.CHF)
        self.assertIn("CHF", str(caught.exception))

    def test_non_positive_rate_is_rejected(self):
        self._write_csv(rows=["United States,Dollar,USD,0,01/01/2025,31/01/2025"])
        with self.assertRaises(ValueError):
            self.provider.get_rate(2025, 1, Currency.USD)

    def test_unrecognised_header_is_rejected(self):
        self._write_csv(rows=["United States,Dollar,USD,1.5"])
        path = self.provider.raw_path(2025, 1)
        # Overwrite with headers that carry no code or rate column.
        _write(path, "Country,Name,Value\nUnited States,Dollar,1.5\n")
        with self.assertRaises(ValueError):
            self.provider.get_rate(2025, 1, Currency.USD)

    def test_labels_paths_and_urls(self):
        self.assertEqual(self.provider.source_label(2025, 1), "trade-tariff:2025-01")
        self.assertEqual(
            self.provider.raw_path(2025, 1),
            self.fx_dir / "2025" / "01" / "trade-tariff-2025-01.csv",
        )
        # Month is not zero-padded in the real Trade Tariff URL.
        self.assertEqual(
            self.provider.download_urls(2025, 1),
            [
                "https://www.trade-tariff.service.gov.uk/exchange_rates/view/files/monthly_csv_2025-1.csv",
            ],
        )


class HmrcXmlProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fx_dir = Path(self.tmp.name)
        self.provider = HmrcXmlProvider(self.fx_dir)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_xml(
        self, year=2020, month=12, period="01/Dec/2020 to 31/Dec/2020", rows=None
    ):
        if rows is None:
            rows = [
                ("USA", "US", "Dollar", "USD", "1.3303"),
                ("Eurozone", "EU", "Euro", "EUR", "1.1202"),
                ("Japan", "JP", "Yen", "JPY", "137.91"),
            ]
        body = "".join(
            "<exchangeRate>"
            f"<countryName>{name}</countryName>"
            f"<countryCode>{code}</countryCode>"
            f"<currencyName>{cname}</currencyName>"
            f"<currencyCode>{ccode}</currencyCode>"
            f"<rateNew>{rate}</rateNew>"
            "</exchangeRate>"
            for name, code, cname, ccode, rate in rows
        )
        text = (
            '<?xml version="1.0"?>\n'
            f'<exchangeRateMonthList Period="{period}">{body}'
            "</exchangeRateMonthList>\n"
        )
        return _write(self.provider.raw_path(year, month), text)

    def test_reads_real_format_rates(self):
        self._write_xml()
        self.assertEqual(
            self.provider.get_rate(2020, 12, Currency.USD), Decimal("1.3303")
        )
        self.assertEqual(
            self.provider.get_rate(2020, 12, Currency.EUR), Decimal("1.1202")
        )
        self.assertEqual(
            self.provider.get_rate(2020, 12, Currency.JPY), Decimal("137.91")
        )

    def test_duplicate_agreeing_rows_are_folded(self):
        # As in the real Feb 2015 file, where the same currency
        # repeats with the same rate.
        self._write_xml(
            year=2015,
            month=2,
            period="01/Feb/2015 to 28/Feb/2015",
            rows=[
                ("USA", "US", "Dollar", "USD", "1.512"),
                ("USA", "US", "Dollar", "USD", "1.512"),
            ],
        )
        self.assertEqual(
            self.provider.get_rate(2015, 2, Currency.USD), Decimal("1.512")
        )

    def test_divergent_duplicate_rows_are_rejected(self):
        self._write_xml(
            rows=[
                ("USA", "US", "Dollar", "USD", "1.3303"),
                ("USA", "US", "Dollar", "USD", "1.3304"),
            ]
        )
        with self.assertRaises(ValueError) as caught:
            self.provider.get_rate(2020, 12, Currency.USD)
        self.assertIn("divergent", str(caught.exception))

    def test_missing_currency_raises(self):
        self._write_xml()
        # CHF is already a known currency (in the enum), just
        # not in this month's file. This tests absence from
        # the file, not absence from the enum, so adding a
        # currency to the enum cannot break it.
        with self.assertRaises(ValueError) as caught:
            self.provider.get_rate(2020, 12, Currency.CHF)
        self.assertIn("CHF", str(caught.exception))

    def test_period_mismatch_is_rejected(self):
        self._write_xml(period="01/Nov/2020 to 30/Nov/2020")
        with self.assertRaises(ValueError) as caught:
            self.provider.get_rate(2020, 12, Currency.USD)
        self.assertIn("does not match", str(caught.exception))

    def test_non_positive_rate_is_rejected(self):
        self._write_xml(rows=[("USA", "US", "Dollar", "USD", "0")])
        with self.assertRaises(ValueError):
            self.provider.get_rate(2020, 12, Currency.USD)

    def test_labels_paths_and_urls(self):
        self.assertEqual(self.provider.source_label(2020, 12), "hmrc-xml:2020-12")
        self.assertEqual(
            self.provider.raw_path(2020, 12),
            self.fx_dir / "2020" / "12" / "hmrc-2020-12.xml",
        )
        self.assertEqual(
            self.provider.download_urls(2020, 12),
            [
                "https://www.hmrc.gov.uk/softwaredevelopers/rates/exrates-monthly-1220.xml",
                "https://www.hmrc.gov.uk/softwaredevelopers/rates/exrates-monthly-1220.XML",
            ],
        )


class UserProvidedRatesProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.user_path = Path(self.tmp.name) / "user-provided.json"
        self.provider = UserProvidedRatesProvider(self.user_path)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_entries(self, entries):
        self.user_path.write_text(json.dumps(entries), encoding="utf-8")

    def _entry(self, year=2019, month=1, currency="USD", rate="9.9"):
        return {
            "year": year,
            "month": month,
            "currency": currency,
            "gbp_to_currency_rate": rate,
        }

    def test_no_rate_when_file_missing_or_empty(self):
        # Missing file and empty list are the same outcome --
        # no entry -- tested in one place, not two.
        with self.assertRaises(UserRateNotFoundError):
            self.provider.get_rate(2019, 1, Currency.USD)
        self._write_entries([])
        provider = UserProvidedRatesProvider(self.user_path)
        with self.assertRaises(UserRateNotFoundError):
            provider.get_rate(2019, 1, Currency.USD)

    def test_reads_rate_currency_case_insensitive(self):
        self._write_entries([self._entry(currency="usd")])
        with redirect_stderr(io.StringIO()):
            rate = self.provider.get_rate(2019, 1, Currency.USD)
        self.assertEqual(rate, Decimal("9.9"))

    def test_warning_printed_once_per_rate(self):
        self._write_entries([self._entry()])
        err = io.StringIO()
        with redirect_stderr(err):
            self.provider.get_rate(2019, 1, Currency.USD)
            self.provider.get_rate(2019, 1, Currency.USD)
        self.assertEqual(err.getvalue().count("user-provided FX rate"), 1)
        self.assertIn("not from HMRC", err.getvalue())

    def test_bad_entry_is_rejected(self):
        # Unknown field, missing field, and a malformed file
        # are all the same outcome -- a bad entry is rejected
        # as ValueError, never masked as a missing rate, for
        # any currency, not just the entry's own. One test,
        # with subTest so each case is reported separately.
        with self.subTest("unknown field"):
            self._write_entries([dict(self._entry(), extra=1)])
            provider = UserProvidedRatesProvider(self.user_path)
            with self.assertRaises(ValueError):
                provider.get_rate(2019, 1, Currency.USD)
            with self.assertRaises(ValueError):
                provider.get_rate(2019, 1, Currency.EUR)
        with self.subTest("missing field"):
            entry = self._entry()
            del entry["month"]
            self._write_entries([entry])
            provider = UserProvidedRatesProvider(self.user_path)
            with self.assertRaises(ValueError):
                provider.get_rate(2019, 1, Currency.USD)

    def test_duplicate_rate_is_rejected(self):
        self._write_entries([self._entry(), self._entry()])
        with self.assertRaises(ValueError) as caught:
            self.provider.get_rate(2019, 1, Currency.USD)
        self.assertIn("duplicate", str(caught.exception))

    def test_bad_year_or_month_is_rejected(self):
        # Month 13, year 0, and a bool year are all the same
        # outcome -- a bad year/month is rejected. One test,
        # with subTest so each case is reported separately.
        # Fresh provider each time: the file is cached on
        # first load, so a new instance is needed after
        # rewriting it.
        with self.subTest("month 13"):
            self._write_entries([self._entry(month=13)])
            with self.assertRaises(ValueError):
                UserProvidedRatesProvider(self.user_path).get_rate(
                    2019, 1, Currency.USD
                )
        with self.subTest("year 0"):
            self._write_entries([self._entry(year=0)])
            with self.assertRaises(ValueError):
                UserProvidedRatesProvider(self.user_path).get_rate(
                    2019, 1, Currency.USD
                )
        with self.subTest("bool year"):
            self._write_entries([self._entry(year=True)])
            with self.assertRaises(TypeError):
                UserProvidedRatesProvider(self.user_path).get_rate(
                    2019, 1, Currency.USD
                )

    def test_malformed_file_is_rejected(self):
        # A file that is not a JSON list, and a file that is
        # not JSON at all, are the same outcome -- the file is
        # rejected, never masked as a missing rate.
        with self.subTest("not a list"):
            self.user_path.write_text(json.dumps({"year": 2019}), encoding="utf-8")
            with self.assertRaises(TypeError):
                UserProvidedRatesProvider(self.user_path).get_rate(
                    2019, 1, Currency.USD
                )
        with self.subTest("not JSON"):
            self.user_path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                UserProvidedRatesProvider(self.user_path).get_rate(
                    2019, 1, Currency.USD
                )

    def test_non_positive_rate_is_rejected(self):
        self._write_entries([self._entry(rate="0")])
        with self.assertRaises(ValueError):
            self.provider.get_rate(2019, 1, Currency.USD)

    def test_unknown_currency_is_rejected(self):
        self._write_entries([self._entry(currency="XXX")])
        with self.assertRaises(ValueError):
            self.provider.get_rate(2019, 1, Currency.USD)

    def test_labels_and_no_download(self):
        self.assertEqual(self.provider.source_label(2019, 1), "user-provided:2019-01")
        self.assertEqual(self.provider.download_urls(2019, 1), [])
        self.assertEqual(self.provider.raw_path(2019, 1), self.user_path)


if __name__ == "__main__":
    unittest.main()
