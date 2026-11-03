"""FX conversion: ParsedEvent to Event, via a context manager.

Dispatch and caching only. Source specifics live in
fx/providers.py, downloading lives in fx/downloader.py.

Rate lookup, in order, one event at a time:
  1. Memory, rates already collected in this run.
  2. Provider, dispatched by year in _provider_for().
  3. UserProvidedRatesProvider, last resort only.
     It never overrides a working provider rate.

UserProvidedRatesProvider is just another provider, local only,
no download component: a single file, <data>/fx/user-provided.json,
a plain list of {"year", "month", "currency",
"gbp_to_currency_rate"} entries, units per GBP.
Ideally empty, [], or missing. A rate from it is labelled
user-provided:YYYY-MM on every Event, and a warning is
printed when it is first used in a run.

On exit, a diagnostic file is written, never read back:
cache/fx/diagnostic.json, rates actually used, with sources.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Self

from fx.providers import (
    FxProvider,
    HmrcXmlProvider,
    TradeTariffProvider,
    UserProvidedRatesProvider,
    UserRateNotFoundError,
)
from models import Currency, Event, ParsedEvent, Pricing


class FxRateMissingError(RuntimeError):
    """Raised when no rate can be found for a month/currency.

    The message itself is the instruction for the exceptional
    case, so main only has to print it and exit 2. It tells
    the user to add a rate to the single user-provided file,
    or to place a source file at the provider's raw path.
    """

    def __init__(
        self,
        currency: Currency,
        year: int,
        month: int,
        user_path: Path,
        raw_path: Path | None,
        provider_error: str,
    ) -> None:
        self.currency = currency
        self.year = year
        self.month = month
        self.user_path = user_path
        self.raw_path = raw_path
        self.provider_error = provider_error
        month_str = f"{year}-{month:02d}"
        lines = [
            f"FX rate missing for {currency.value} {month_str}.",
            f"Provider error: {provider_error}",
            "Provider was tried first, then the user-provided file, neither had this rate.",
            "To proceed, in this exceptional case only, provide the rate",
            "manually, as original units per GBP, exactly as HMRC publishes",
            "it, not inverted, in one of these explicit ways, then re-run:",
            f"  1) Append an entry to the single user-provided file at {user_path},",
            "     with this shape, one entry per rate:",
            "     [",
            "       {",
            f'         "year": {year},',
            f'         "month": {month},',
            f'         "currency": "{currency.value}",',
            '         "gbp_to_currency_rate": "<rate>"',
            "       }",
            "     ]",
            "     Only the currencies you need are required in that file.",
            "     Ideally this file is empty, [], meaning no manual",
            "     rates were needed. A rate from it will be labelled",
            "     user-provided in all reporting, so it is visible,",
            "     not hidden as an HMRC rate.",
        ]
        if raw_path is not None:
            lines.append(
                f"  2) Or download the original monthly source file yourself "
                f"and place it at {raw_path}, then re-run."
            )
        lines.append(
            "Cache is for the CLI only. User rates go in the "
            "user-provided file, source files go at their raw path."
        )
        super().__init__("\n".join(lines))


class FxConverter:
    """Dispatch and caching only. Source specifics are providers."""

    def __init__(self, cache_dir: Path, user_path: Path) -> None:
        self.cache_dir = cache_dir
        self.fx_dir = cache_dir / "fx"
        self.user_path = user_path
        self.diagnostic_path = self.fx_dir / "diagnostic.json"
        # (year, month, currency) -> (original_units_per_gbp, fx_source)
        self._rates: dict[tuple[int, int, Currency], tuple[Decimal, str]] = {}
        self._user_provider = UserProvidedRatesProvider(user_path)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Diagnostic write only, never suppresses the original exception.
        try:
            self._write_diagnostic()
        except Exception as write_exc:  # noqa: BLE001
            print(
                f"Warning: could not write FX diagnostic: {write_exc}",
                file=sys.stderr,
            )
        return False

    def _provider_for(self, year: int) -> FxProvider | None:
        # Single dispatch table, disjoint year ranges.
        if year >= 2021:
            return TradeTariffProvider(self.fx_dir)
        if 2015 <= year < 2021:
            return HmrcXmlProvider(self.fx_dir)
        return None

    # -- conversion, one by one --

    def get_rate(
        self, currency: Currency, year: int, month: int
    ) -> tuple[Decimal, str]:
        if currency == Currency.GBP:
            return Decimal(1), "GBP"
        key = (year, month, currency)
        if key in self._rates:
            return self._rates[key]
        # Dedicated provider first. If it fails, or there is
        # no provider for this year, fall through to the
        # user provider, last resort only.
        provider = self._provider_for(year)
        provider_error: str | None = None
        raw_path: Path | None = None
        if provider is None:
            provider_error = f"no FX provider is defined for year {year}"
        else:
            raw_path = provider.raw_path(year, month)
            try:
                rate = provider.get_rate(year, month, currency)
            except Exception as exc:  # noqa: BLE001
                provider_error = str(exc)
            else:
                source = provider.source_label(year, month)
                self._rates[key] = (rate, source)
                return self._rates[key]
        try:
            rate = self._user_provider.get_rate(year, month, currency)
        except UserRateNotFoundError:
            raise FxRateMissingError(
                currency,
                year,
                month,
                self.user_path,
                raw_path,
                provider_error or "no rate found",
            )
        # Malformed user file errors (ValueError, TypeError)
        # are not caught here, they propagate to main as-is.
        source = self._user_provider.source_label(year, month)
        self._rates[key] = (rate, source)
        return self._rates[key]

    def convert_event(self, parsed: ParsedEvent) -> Event:
        rate, source = self.get_rate(
            parsed.original_currency, parsed.date.year, parsed.date.month
        )
        pricing = Pricing(
            original_currency=parsed.original_currency,
            original_price=parsed.original_price,
            original_amount=parsed.original_amount,
            original_fees=parsed.original_fees,
            original_units_per_gbp=rate,
            fx_source=source,
        )
        return Event(
            date=parsed.date,
            action=parsed.action,
            symbol=parsed.symbol,
            description=parsed.description,
            quantity=parsed.quantity,
            pricing=pricing,
            provider=parsed.provider,
            source_file=parsed.source_file,
            row=parsed.row,
        )

    def convert_events(self, parsed_events: list[ParsedEvent]) -> list[Event]:
        return [self.convert_event(parsed) for parsed in parsed_events]

    def _write_diagnostic(self) -> None:
        # Overwritten each run, never read back.
        self.fx_dir.mkdir(parents=True, exist_ok=True)
        entries = []
        for (year, month, currency), (rate, source) in sorted(self._rates.items()):
            entries.append(
                {
                    "month": f"{year}-{month:02d}",
                    "currency": currency.value,
                    "original_units_per_gbp": str(rate),
                    "fx_source": source,
                }
            )
        data = {"rates_used": entries}
        self.diagnostic_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )
