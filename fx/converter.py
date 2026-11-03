"""FX conversion: ParsedEvent to Event, via a context manager.

Dispatch and caching only. Source specifics live in
fx/providers.py, downloading lives in fx/downloader.py.

Rate lookup, in order, one event at a time:
  1. Memory, rates already collected in this run.
  2. Provider, dispatched by year in _provider_for().
  3. User-provided file, last resort only, see below.
     It never overrides a working provider rate.

User-provided: a single file, <data>/fx/user-provided.json,
{"YYYY-MM": {"USD": "<rate>"}}, units per GBP.
Ideally empty, {}, or missing. A rate from it is labelled
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

from fx.providers import FxProvider, HmrcXmlProvider, TradeTariffProvider
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
            f"  1) Add it to the single user-provided file at {user_path},",
            f"     under the {month_str} key, with this shape:",
            f'     {{"{month_str}": {{"{currency.value}": "<rate>"}}}}',
            "     Only the currencies you need are required in that file.",
            "     Ideally this file is empty, {}, meaning no manual",
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
        # Single user file, loaded once per run, or None if not loaded yet.
        # Missing file means empty, no error, that is the ideal state.
        self._user_rates: dict[tuple[int, int, Currency], Decimal] | None = None

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

    # -- user-provided layer, single file, last resort --

    def _load_user_file(self) -> dict[tuple[int, int, Currency], Decimal]:
        if self._user_rates is not None:
            return self._user_rates
        path = self.user_path
        if not path.is_file():
            self._user_rates = {}
            return self._user_rates
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError(
                f"user-provided file {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise TypeError(
                f"user-provided file {path} is not a JSON object, "
                f'expected {{"YYYY-MM": {{"USD": "<rate>"}}}}'
            )
        rates: dict[tuple[int, int, Currency], Decimal] = {}
        for month_text, month_rates_raw in data.items():
            parts = str(month_text).strip().split("-")
            if len(parts) != 2:
                raise ValueError(
                    f"user-provided file {path}: bad month key {month_text!r}, "
                    "expected YYYY-MM"
                )
            try:
                year = int(parts[0])
                month = int(parts[1])
            except ValueError:
                raise ValueError(
                    f"user-provided file {path}: bad month key {month_text!r}, "
                    "expected YYYY-MM"
                ) from None
            if not 1 <= month <= 12:
                raise ValueError(f"user-provided file {path}: bad month {month_text!r}")
            if not isinstance(month_rates_raw, dict):
                raise TypeError(
                    f"user-provided file {path}: rates for {month_text} are not an object"
                )
            for code_text, rate_text in month_rates_raw.items():
                try:
                    currency = Currency(str(code_text).strip().upper())
                except ValueError:
                    raise ValueError(
                        f"user-provided file {path}: unknown currency "
                        f"{code_text!r} for {month_text}"
                    ) from None
                try:
                    rate = Decimal(str(rate_text).strip())
                except Exception as exc:  # noqa: BLE001
                    raise ValueError(
                        f"user-provided file {path}: bad rate for "
                        f"{code_text} {month_text}: {rate_text!r}"
                    ) from exc
                if rate <= 0:
                    raise ValueError(
                        f"user-provided file {path}: non-positive rate for "
                        f"{code_text} {month_text}: {rate}"
                    )
                rates[(year, month, currency)] = rate
        self._user_rates = rates
        return self._user_rates

    def _user_rate(self, currency: Currency, year: int, month: int) -> Decimal | None:
        """Last-resort rate. Warns on stderr once per distinct rate used."""

        rates = self._load_user_file()
        rate = rates.get((year, month, currency))
        if rate is None:
            return None
        month_str = f"{year}-{month:02d}"
        print(
            f"Warning: using user-provided FX rate for {currency.value} "
            f"{month_str}: {rate} units per GBP, from {self.user_path}, "
            f"not from HMRC. This rate is manual and will be labelled "
            f"user-provided:{month_str} in all reporting.",
            file=sys.stderr,
        )
        return rate

    # -- conversion, one by one --

    def get_rate(
        self, currency: Currency, year: int, month: int
    ) -> tuple[Decimal, str]:
        if currency == Currency.GBP:
            return Decimal(1), "GBP"
        key = (year, month, currency)
        if key in self._rates:
            return self._rates[key]
        month_str = f"{year}-{month:02d}"
        # Provider first. Only if it fails, or does not
        # contain this currency, do we fall through to
        # the user file, as last resort. One currency
        # at a time, no month dict in the converter.
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
        # Last resort: user-provided file.
        user_rate = self._user_rate(currency, year, month)
        if user_rate is not None:
            source = f"user-provided:{month_str}"
            self._rates[key] = (user_rate, source)
            return self._rates[key]
        raise FxRateMissingError(
            currency,
            year,
            month,
            self.user_path,
            raw_path,
            provider_error or "no rate found",
        )

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
