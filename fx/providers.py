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
from abc import ABC, abstractmethod
from decimal import Decimal
from pathlib import Path

from fx.downloader import download_file
from models import Currency


class FxProvider(ABC):
    """A source for one currency/month rate."""

    def __init__(self, fx_dir: Path) -> None:
        self.fx_dir = fx_dir

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
        if not raw_path.is_file():
            download_file(self.download_urls(year, month), raw_path)
        rates = self._parse(raw_path)
        if currency not in rates:
            raise ValueError(f"{raw_path}: month file did not contain {currency.value}")
        return rates[currency]

    def _parse(self, raw_path: Path) -> dict[Currency, Decimal]:
        # Header, verified against the real file:
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
    """Historic source, 2015 to 2020 inclusive, HMRC XML per month."""

    def source_label(self, year: int, month: int) -> str:
        return f"hmrc-xml:{year}-{month:02d}"

    def raw_path(self, year: int, month: int) -> Path:
        return self.fx_dir / str(year) / f"{month:02d}" / f"hmrc-{year}-{month:02d}.xml"

    def download_urls(self, year: int, month: int) -> list[str]:
        base = (
            "https://www.hmrc.gov.uk/softwaredevelopers/"
            f"{year}/{month:02d}{str(year)[-2:]}"
        )
        return [base + ".XML", base + ".xml"]

    def get_rate(self, year: int, month: int, currency: Currency) -> Decimal:
        raw_path = self.raw_path(year, month)
        if not raw_path.is_file():
            download_file(self.download_urls(year, month), raw_path)
        return self._parse(raw_path, currency)

    def _parse(self, raw_path: Path, currency: Currency) -> Decimal:
        raise NotImplementedError("HMRC XML parse not implemented: format unverified")
