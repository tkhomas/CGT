"""Tests for FxConverter in fx/converter.py.

Dispatch, lookup order, conversion to GBP, and the diagnostic
file. Provider files are fake, pre-written at their raw paths
under a temporary cache dir, so no test touches the network.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fx.converter import FxConverter, FxRateMissingError
from models import Action, Currency, ParsedEvent


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _trade_tariff_csv(rows: list[str]) -> str:
    return (
        "Country/Territories,Currency,Currency Code,"
        "Currency Units per £1,Start date,End date\n" + "\n".join(rows) + "\n"
    )


def _hmrc_xml(period: str, rows: list[tuple[str, str, str, str, str]]) -> str:
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
    return (
        '<?xml version="1.0"?>\n'
        f'<exchangeRateMonthList Period="{period}">{body}'
        "</exchangeRateMonthList>\n"
    )


def _parsed_event(
    currency: Currency = Currency.USD,
    when: date = date(2025, 1, 15),
    price: Decimal = Decimal("127.07"),
    amount: Decimal = Decimal("1270.70"),
    fees: Decimal = Decimal("1.00"),
    quantity: int = 10,
) -> ParsedEvent:
    return ParsedEvent(
        date=when,
        action=Action.BUY,
        symbol="TEST",
        description="",
        quantity=quantity,
        original_currency=currency,
        original_price=price,
        original_amount=amount,
        original_fees=fees,
        provider="Test Broker",
        source_file="test.csv",
        row=2,
    )


class FxConverterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.cache_dir = self.base / "cache"
        self.user_path = self.base / "fx" / "user-provided.json"
        self.fx_dir = self.cache_dir / "fx"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _converter(self) -> FxConverter:
        return FxConverter(self.cache_dir, self.user_path)

    def _write_trade_tariff(self, year: int, month: int, rows: list[str]) -> Path:
        path = (
            self.fx_dir
            / str(year)
            / f"{month:02d}"
            / f"trade-tariff-{year}-{month:02d}.csv"
        )
        return _write(path, _trade_tariff_csv(rows))

    def _write_hmrc(
        self,
        year: int,
        month: int,
        period: str,
        rows: list[tuple[str, str, str, str, str]],
    ) -> Path:
        path = self.fx_dir / str(year) / f"{month:02d}" / f"hmrc-{year}-{month:02d}.xml"
        return _write(path, _hmrc_xml(period, rows))

    def _write_user(self, entries: list[dict]) -> None:
        _write(self.user_path, json.dumps(entries))

    def test_gbp_shortcut_needs_no_rate(self) -> None:
        with self._converter() as fx:
            self.assertEqual(fx.get_rate(Currency.GBP, 2025, 1), (Decimal(1), "GBP"))

    def test_dispatch_2021_and_later_is_trade_tariff(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        with self._converter() as fx:
            rate, source = fx.get_rate(Currency.USD, 2025, 1)
        self.assertEqual(rate, Decimal("1.2707"))
        self.assertEqual(source, "trade-tariff:2025-01")

    def test_dispatch_2015_to_2020_is_hmrc_xml(self) -> None:
        self._write_hmrc(
            2020,
            12,
            "01/Dec/2020 to 31/Dec/2020",
            [("USA", "US", "Dollar", "USD", "1.3303")],
        )
        with self._converter() as fx:
            rate, source = fx.get_rate(Currency.USD, 2020, 12)
        self.assertEqual(rate, Decimal("1.3303"))
        self.assertEqual(source, "hmrc-xml:2020-12")

    def test_dispatch_before_2015_has_no_provider(self) -> None:
        self._write_user(
            [
                {
                    "year": 2014,
                    "month": 6,
                    "currency": "USD",
                    "gbp_to_currency_rate": "1.5",
                }
            ]
        )
        with self._converter() as fx:
            rate, source = fx.get_rate(Currency.USD, 2014, 6)
        self.assertEqual(rate, Decimal("1.5"))
        self.assertEqual(source, "user-provided:2014-06")

    def test_user_rate_never_overrides_a_working_provider(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        self._write_user(
            [
                {
                    "year": 2025,
                    "month": 1,
                    "currency": "USD",
                    "gbp_to_currency_rate": "9.9",
                }
            ]
        )
        with self._converter() as fx:
            rate, source = fx.get_rate(Currency.USD, 2025, 1)
        self.assertEqual(rate, Decimal("1.2707"))
        self.assertEqual(source, "trade-tariff:2025-01")

    def test_provider_failure_falls_through_to_user_rate(self) -> None:
        # HMRC file exists but does not contain EUR; the user
        # file does. The user rate is last resort, not a
        # replacement for a working provider.
        self._write_hmrc(
            2020,
            12,
            "01/Dec/2020 to 31/Dec/2020",
            [("USA", "US", "Dollar", "USD", "1.3303")],
        )
        self._write_user(
            [
                {
                    "year": 2020,
                    "month": 12,
                    "currency": "EUR",
                    "gbp_to_currency_rate": "1.1202",
                }
            ]
        )
        with self._converter() as fx:
            rate, source = fx.get_rate(Currency.EUR, 2020, 12)
        self.assertEqual(rate, Decimal("1.1202"))
        self.assertEqual(source, "user-provided:2020-12")

    def test_missing_rate_raises_with_instructions(self) -> None:
        # The provider file is pre-written but does not contain
        # USD, so this fails locally without downloading the
        # real monthly file. A missing file would download in
        # an environment with network access and pass there
        # while failing offline, so tests must never rely on
        # a file simply being absent.
        self._write_trade_tariff(
            2025, 1, ["Eurozone,Euro,EUR,1.2106,01/01/2025,31/01/2025"]
        )
        with self._converter() as fx, self.assertRaises(FxRateMissingError) as caught:
            fx.get_rate(Currency.USD, 2025, 1)
        message = str(caught.exception)
        self.assertIn("FX rate missing for USD 2025-01", message)
        self.assertIn(str(self.user_path), message)
        # A dedicated raw path exists for 2025, so the message
        # must offer placing the source file there.
        self.assertIn("trade-tariff-2025-01.csv", message)

    def test_missing_rate_before_2015_has_no_raw_path_option(self) -> None:
        with self._converter() as fx, self.assertRaises(FxRateMissingError) as caught:
            fx.get_rate(Currency.USD, 2014, 6)
        self.assertNotIn("download the original", str(caught.exception))

    def test_malformed_user_file_propagates_not_masked_as_missing(self) -> None:
        # Pre-write a provider file that does not contain USD,
        # so the converter reaches the user file without
        # downloading the real monthly file first.
        self._write_trade_tariff(
            2025, 1, ["Eurozone,Euro,EUR,1.2106,01/01/2025,31/01/2025"]
        )
        self.user_path.parent.mkdir(parents=True, exist_ok=True)
        self.user_path.write_text('{"year": 2025}', encoding="utf-8")
        with self._converter() as fx, self.assertRaises(TypeError):
            fx.get_rate(Currency.USD, 2025, 1)

    def test_rate_is_cached_in_memory(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        with self._converter() as fx:
            first = fx.get_rate(Currency.USD, 2025, 1)
            # Delete the raw file: the second lookup must still
            # succeed from the in-memory cache.
            (self.fx_dir / "2025" / "01" / "trade-tariff-2025-01.csv").unlink()
            second = fx.get_rate(Currency.USD, 2025, 1)
        self.assertEqual(first, second)

    def test_convert_event_divides_by_rate(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        with self._converter() as fx:
            event = fx.convert_event(_parsed_event())
        # $127.07 at 1.2707 units per GBP is exactly £100.
        self.assertEqual(event.pricing.price, Decimal(100))
        self.assertEqual(event.pricing.amount, Decimal(1000))
        # Fees are converted the same way, not left in USD.
        self.assertEqual(event.pricing.fees, Decimal("1.00") / Decimal("1.2707"))
        self.assertEqual(event.pricing.original_units_per_gbp, Decimal("1.2707"))
        self.assertEqual(event.pricing.fx_source, "trade-tariff:2025-01")
        self.assertEqual(event.pricing.currency, Currency.GBP)
        self.assertEqual(event.pricing.original_currency, Currency.USD)
        self.assertEqual(event.symbol, "TEST")
        self.assertEqual(event.quantity, 10)

    def test_convert_events_preserves_order(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        with self._converter() as fx:
            events = fx.convert_events(
                [_parsed_event(), _parsed_event(when=date(2025, 1, 20))]
            )
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].date, date(2025, 1, 15))
        self.assertEqual(events[1].date, date(2025, 1, 20))

    def test_diagnostic_is_written_sorted_and_gbp_is_not_listed(self) -> None:
        self._write_trade_tariff(
            2025,
            1,
            [
                "United States,Dollar,USD,1.2707,01/01/2025,31/01/2025",
                "Eurozone,Euro,EUR,1.2106,01/01/2025,31/01/2025",
            ],
        )
        self._write_hmrc(
            2020,
            12,
            "01/Dec/2020 to 31/Dec/2020",
            [("USA", "US", "Dollar", "USD", "1.3303")],
        )
        with self._converter() as fx:
            fx.get_rate(Currency.EUR, 2025, 1)
            fx.get_rate(Currency.USD, 2020, 12)
            fx.get_rate(Currency.USD, 2025, 1)
            fx.get_rate(Currency.GBP, 2025, 1)
        diagnostic = json.loads(
            (self.fx_dir / "diagnostic.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            diagnostic,
            {
                "rates_used": [
                    {
                        "month": "2020-12",
                        "currency": "USD",
                        "original_units_per_gbp": "1.3303",
                        "fx_source": "hmrc-xml:2020-12",
                    },
                    {
                        "month": "2025-01",
                        "currency": "EUR",
                        "original_units_per_gbp": "1.2106",
                        "fx_source": "trade-tariff:2025-01",
                    },
                    {
                        "month": "2025-01",
                        "currency": "USD",
                        "original_units_per_gbp": "1.2707",
                        "fx_source": "trade-tariff:2025-01",
                    },
                ]
            },
        )

    def test_exit_does_not_suppress_an_exception(self) -> None:
        self._write_trade_tariff(
            2025, 1, ["United States,Dollar,USD,1.2707,01/01/2025,31/01/2025"]
        )
        with self.assertRaises(RuntimeError), self._converter() as fx:
            fx.get_rate(Currency.USD, 2025, 1)
            raise RuntimeError("boom")
        # The diagnostic is still written on the way out.
        self.assertTrue((self.fx_dir / "diagnostic.json").is_file())


if __name__ == "__main__":
    unittest.main()
