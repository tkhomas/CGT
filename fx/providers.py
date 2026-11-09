"""Concrete FX providers.

Interface:
    provider = TradeTariffProvider(fx_dir)
    rate = provider.get_rate(year, month, currency)

Year, month, currency in, original units per GBP out.
The fx path is passed once, at construction.

A provider declares only what it needs: its raw path,
its download URLs in order, and its source label.
How a file is downloaded lives in fx/downloader.py,
a provider does not care. How a file is parsed lives
in the provider, because formats differ per source.

Rates are original units per GBP, exactly as published,
not inverted.
"""

from __future__ import annotations

import csv
import json
import sys
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path

from fx.downloader import download_file
from models import Currency


class UserRateNotFoundError(LookupError):
    """The user file exists, or is missing, but has no rate for this month/currency.

    Distinct from a malformed user file, which raises ValueError
    or TypeError and should be reported as-is, not as a missing rate.
    """


class FxProvider(ABC):
    """A source for one currency/month rate."""

    def __init__(self, fx_dir: Path, force_download: bool = False) -> None:
        self.fx_dir = fx_dir
        # True: ignore a cached raw file and redownload it.
        # False: download only when the raw file is missing.
        self.force_download = force_download

    @abstractmethod
    def source_label(self, year: int, month: int) -> str:
        """Label for Pricing.fx_source, e.g. trade-tariff:2026-11."""

    @abstractmethod
    def raw_path(self, year: int, month: int) -> Path:
        """Path of this provider's raw file for this month."""

    @abstractmethod
    def download_urls(self, year: int, month: int) -> list[str]:
        """URLs to try, in order, for this month's raw file."""

    @abstractmethod
    def get_rate(self, year: int, month: int, currency: Currency) -> Decimal:
        """Return units per GBP, or raise on any failure."""
        raise NotImplementedError


class TradeTariffProvider(FxProvider):
    """Current source, from 2021 onwards, trade-tariff CSV."""

    def source_label(self, year: int, month: int) -> str:
        return f"trade-tariff:{year}-{month:02d}"

    def raw_path(self, year: int, month: int) -> Path:
        return (
            self.fx_dir
            / str(year)
            / f"{month:02d}"
            / f"trade-tariff-{year}-{month:02d}.csv"
        )

    def download_urls(self, year: int, month: int) -> list[str]:
        return [
            (
                "https://www.trade-tariff.service.gov.uk/exchange_rates/"
                f"view/files/monthly_csv_{year}-{month}.csv"
            )
        ]

    def get_rate(self, year: int, month: int, currency: Currency) -> Decimal:
        raw_path = self.raw_path(year, month)
        if self.force_download or not raw_path.is_file():
            download_file(self.download_urls(year, month), raw_path)
        rates = self._parse(raw_path)
        if currency not in rates:
            raise ValueError(f"{raw_path}: month file did not contain {currency.value}")
        return rates[currency]

    def _parse(self, raw_path: Path) -> dict[Currency, Decimal]:
        # Expected header:
        # Country/Territories,Currency,Currency Code,
        # Currency Units per £1,Start date,End date
        with raw_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError(f"{raw_path}: no header row found")
            normalised = {
                (name or "").strip().lower(): name for name in reader.fieldnames
            }
            code_key = next(
                (
                    normalised[c]
                    for c in ("currency code", "currency_code", "code")
                    if c in normalised
                ),
                None,
            )
            if code_key is None:
                code_key = next((v for k, v in normalised.items() if "code" in k), None)
            rate_key = next(
                (
                    v
                    for k, v in normalised.items()
                    if "per" in k and ("£" in k or "gbp" in k or "pound" in k)
                ),
                None,
            )
            if rate_key is None:
                rate_key = next(
                    (v for k, v in normalised.items() if "rate" in k or "units" in k),
                    None,
                )
            if code_key is None or rate_key is None:
                raise ValueError(
                    f"{raw_path}: unrecognised CSV headers {reader.fieldnames}"
                )
            rates: dict[Currency, Decimal] = {}
            for row in reader:
                code_text = (row.get(code_key) or "").strip().upper()
                if not code_text:
                    continue
                try:
                    currency = Currency(code_text)
                except ValueError:
                    continue
                rate_text = (row.get(rate_key) or "").strip()
                if not rate_text:
                    continue
                rate = Decimal(rate_text.replace(",", ""))
                if rate <= 0:
                    raise ValueError(
                        f"{raw_path}: non-positive rate for {code_text}: {rate}"
                    )
                rates[currency] = rate
        if not rates:
            raise ValueError(f"{raw_path}: no known currencies found")
        return rates


class HmrcXmlProvider(FxProvider):
    """Historic source, 2015 to 2020 inclusive, HMRC XML per month.

    Monthly file at
    https://www.hmrc.gov.uk/softwaredevelopers/rates/
    exrates-monthly-MMYY.xml, e.g. Dec 2020 is
    exrates-monthly-1220.xml.

    Structure:
    <exchangeRateMonthList Period="01/Dec/2020 to 31/Dec/2020">
        <exchangeRate>
        <countryName>USA</countryName>
        <countryCode>US</countryCode>
        <currencyName>Dollar</currencyName>
        <currencyCode>USD</currencyCode>
        <rateNew>1.3303</rateNew>
        </exchangeRate>
        ...
    </exchangeRateMonthList>
    No namespace, no encoding in the legacy declaration
    (<?xml version="1.0"?>), rateNew is units per GBP.
    A currency can repeat across countries: repeated rows
    for the requested currency are folded only if every rate
    agrees as Decimal, otherwise the file is rejected, never
    silently last-wins.
    """

    _MONTH_ABBRS = (
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

    def source_label(self, year: int, month: int) -> str:
        return f"hmrc-xml:{year}-{month:02d}"

    def raw_path(self, year: int, month: int) -> Path:
        return self.fx_dir / str(year) / f"{month:02d}" / f"hmrc-{year}-{month:02d}.xml"

    def download_urls(self, year: int, month: int) -> list[str]:
        base = (
            "https://www.hmrc.gov.uk/softwaredevelopers/rates/"
            f"exrates-monthly-{month:02d}{str(year)[-2:]}"
        )
        return [base + ".xml", base + ".XML"]

    def get_rate(self, year: int, month: int, currency: Currency) -> Decimal:
        raw_path = self.raw_path(year, month)
        if self.force_download or not raw_path.is_file():
            download_file(self.download_urls(year, month), raw_path)
        return self._parse(raw_path, currency, year, month)

    def _parse(
        self, raw_path: Path, currency: Currency, year: int, month: int
    ) -> Decimal:
        try:
            root = ET.parse(raw_path).getroot()
        except ET.ParseError as exc:
            raise ValueError(f"{raw_path}: invalid XML: {exc}") from exc
        if root.tag != "exchangeRateMonthList":
            raise ValueError(
                f"{raw_path}: unexpected root element {root.tag!r}, "
                "expected exchangeRateMonthList"
            )
        self._check_period(raw_path, root.get("Period"), year, month)
        rate: Decimal | None = None
        for row in root.findall("exchangeRate"):
            code_text = row.findtext("currencyCode")
            if code_text is None:
                continue
            try:
                row_currency = Currency(code_text.strip().upper())
            except ValueError:
                # Not in our enum, e.g. ARS, XOF: not an error,
                # the file simply carries more currencies than we use.
                continue
            if row_currency != currency:
                continue
            rate_text = row.findtext("rateNew")
            if rate_text is None or not rate_text.strip():
                raise ValueError(f"{raw_path}: missing rateNew for {currency.value}")
            try:
                row_rate = Decimal(rate_text.strip())
            except Exception as exc:
                raise ValueError(
                    f"{raw_path}: bad rate for {currency.value}: {rate_text!r}"
                ) from exc
            if row_rate <= 0:
                raise ValueError(
                    f"{raw_path}: non-positive rate for {currency.value}: {row_rate}"
                )
            if rate is not None and row_rate != rate:
                raise ValueError(
                    f"{raw_path}: divergent rates for {currency.value}: "
                    f"{rate} and {row_rate}"
                )
            rate = row_rate
        if rate is None:
            raise ValueError(f"{raw_path}: month file did not contain {currency.value}")
        return rate

    def _check_period(
        self, raw_path: Path, period: str | None, year: int, month: int
    ) -> None:
        # A served file for the wrong month must not silently
        # give a wrong rate. Month names are mapped explicitly,
        # not via strptime %b, which is locale-dependent.
        if period is None:
            raise ValueError(f"{raw_path}: missing Period attribute")
        try:
            start = period.split(" to ")[0]
            _day, mon, period_year = start.split("/")
            period_month = self._MONTH_ABBRS.index(mon) + 1
            period_year_int = int(period_year)
        except Exception as exc:
            raise ValueError(f"{raw_path}: bad Period {period!r}") from exc
        if (period_year_int, period_month) != (year, month):
            raise ValueError(
                f"{raw_path}: Period {period!r} does not match "
                f"requested {year}-{month:02d}"
            )


class UserProvidedRatesProvider(FxProvider):
    """Local-only provider, no download component.

    Single file, <data>/fx/user-provided.json, a plain list,
    one entry per rate, to append to just add an entry:
      [
        {
          "year": 2019,
          "month": 1,
          "currency": "USD",
          "gbp_to_currency_rate": "9.9"
        }
      ]
    gbp_to_currency_rate is original units per GBP, exactly
    as HMRC publishes it, not inverted. Ideally empty, [],
    or missing. A rate from it is labelled
    user-provided:YYYY-MM, and a warning is printed when it
    is first used in a run, once per distinct rate.
    """

    _FIELDS = ("year", "month", "currency", "gbp_to_currency_rate")

    def __init__(self, user_path: Path) -> None:
        self.user_path = user_path
        self._rates: dict[tuple[int, int, Currency], Decimal] | None = None
        self._warned: set[tuple[int, int, Currency]] = set()

    def source_label(self, year: int, month: int) -> str:
        return f"user-provided:{year}-{month:02d}"

    def raw_path(self, year: int, month: int) -> Path:
        # Interface completeness only: this is the local file,
        # not a downloadable raw file.
        return self.user_path

    def download_urls(self, year: int, month: int) -> list[str]:
        return []

    def get_rate(self, year: int, month: int, currency: Currency) -> Decimal:
        rates = self._load()
        rate = rates.get((year, month, currency))
        if rate is None:
            raise UserRateNotFoundError(
                f"user-provided file has no rate for {currency.value} {year}-{month:02d}"
            )
        key = (year, month, currency)
        if key not in self._warned:
            self._warned.add(key)
            print(
                f"Warning: using user-provided FX rate for {currency.value} "
                f"{year}-{month:02d}: {rate} units per GBP, from {self.user_path}, "
                f"not from HMRC. This rate is manual and will be labelled "
                f"user-provided:{year}-{month:02d} in all reporting.",
                file=sys.stderr,
            )
        return rate

    def _load(self) -> dict[tuple[int, int, Currency], Decimal]:
        if self._rates is not None:
            return self._rates
        path = self.user_path
        if not path.is_file():
            self._rates = {}
            return self._rates
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(
                f"user-provided file {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, list):
            raise TypeError(
                f"user-provided file {path} is not a JSON list, "
                'expected [{"year": ..., "month": ..., "currency": ..., '
                '"gbp_to_currency_rate": ...}]'
            )
        rates: dict[tuple[int, int, Currency], Decimal] = {}
        for index, entry in enumerate(data):
            where = f"user-provided file {path}, entry {index}"
            if not isinstance(entry, dict):
                raise TypeError(f"{where} is not an object")
            unknown = sorted(set(entry) - set(self._FIELDS))
            if unknown:
                raise ValueError(f"{where}: unknown field(s) {unknown}")
            missing = [f for f in self._FIELDS if f not in entry]
            if missing:
                raise ValueError(f"{where}: missing field(s) {missing}")
            year = self._parse_int(entry["year"], where, "year")
            month = self._parse_int(entry["month"], where, "month")
            if year <= 0:
                raise ValueError(f"{where}: bad year {entry['year']!r}")
            if not 1 <= month <= 12:
                raise ValueError(f"{where}: bad month {entry['month']!r}")
            try:
                currency = Currency(str(entry["currency"]).strip().upper())
            except ValueError:
                raise ValueError(
                    f"{where}: unknown currency {entry['currency']!r}"
                ) from None
            raw_rate = entry["gbp_to_currency_rate"]
            if isinstance(raw_rate, bool) or raw_rate is None:
                raise ValueError(f"{where}: bad rate {raw_rate!r}")
            try:
                rate = Decimal(str(raw_rate).strip())
            except Exception as exc:
                raise ValueError(f"{where}: bad rate {raw_rate!r}") from exc
            if rate <= 0:
                raise ValueError(f"{where}: non-positive rate {rate}")
            key = (year, month, currency)
            if key in rates:
                raise ValueError(
                    f"{where}: duplicate rate for {currency.value} {year}-{month:02d}"
                )
            rates[key] = rate
        self._rates = rates
        return self._rates

    @staticmethod
    def _parse_int(value: object, where: str, field: str) -> int:
        if isinstance(value, bool):
            raise TypeError(f"{where}: bad {field} {value!r}")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        raise ValueError(f"{where}: bad {field} {value!r}")
