#!/usr/bin/env python3
"""First step: parse provider CSVs into internal events.

Layout, with --data-dir defaulting to "data":
  <data-dir>/input/<provider>/config.json   required per provider
  <data-dir>/input/<provider>/...           CSVs, any layout, recursive
  <data-dir>/output/                        reports (later steps)
  <data-dir>/output/cache/                  internal cache, not a CLI option
"""

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path


class Action(Enum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"


class Currency(Enum):
    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"
    CHF = "CHF"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"


@dataclass
class ProviderConfig:
    name: str  # folder name under input/, not a field in config.json
    date_format: str
    currency: Currency


@dataclass
class ProviderInput:
    path: Path
    provider: ProviderConfig


@dataclass
class Event:
    date: date
    action: Action
    symbol: str
    description: str
    quantity: int
    price: Decimal
    fees: Decimal
    amount: Decimal
    currency: Currency
    provider: str
    source_file: str
    row: int


def parse_date(text, date_format):
    # Returns a native date object, not a string. Internally the
    # date is unambiguous; format it as ISO YYYY-MM-DD only on output.
    return datetime.strptime(text.strip(), date_format).date()


def parse_action(text, source_file, row):
    # Returns an Action, or None for an unrecognised action.
    # Unrecognised actions warn at parse time and are skipped by
    # the caller, never silently dropped.
    key = (text or "").strip().lower()
    for action in Action:
        if action.value == key:
            return action
    print(
        f"Warning: {source_file}:{row}: unrecognised Action {text!r}, skipped",
        file=sys.stderr,
    )
    return None


def parse_money(text):
    # Strip currency symbols and thousands separators. This is
    # formatting cleanup only: it does NOT decide the currency.
    # "$" alone is ambiguous (USD, MXN, AUD and others all use it),
    # so the currency always comes from the provider config.
    # Symbols stripped here: $, £, €, ¥, and similar.
    if text is None or text.strip() == "":
        return Decimal(0)
    cleaned = text.strip()
    for symbol in ("$", "£", "€", "¥", "₹"):
        cleaned = cleaned.replace(symbol, "")
    return Decimal(cleaned.replace(",", "").strip())


def load_provider_config(provider_dir):
    """Load <provider>/config.json, or None with a warning."""
    config_path = provider_dir / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"cannot read config.json ({exc}), provider skipped",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json is not an object, provider skipped",
            file=sys.stderr,
        )
        return None
    date_format = data.get("date_format")
    if not isinstance(date_format, str) or not date_format:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json missing date_format, provider skipped",
            file=sys.stderr,
        )
        return None
    try:
        currency = Currency(data.get("currency"))
    except ValueError:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json has unknown currency "
            f"{data.get('currency')!r}, provider skipped",
            file=sys.stderr,
        )
        return None
    return ProviderConfig(
        name=provider_dir.name,
        date_format=date_format,
        currency=currency,
    )


def parse_csv(path, provider):
    events = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = parse_action(row.get("Action"), path.name, reader.line_num)
            if action is None:
                skipped += 1
                continue
            if action != Action.SELL:
                # Known action, but not processed in this first step.
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"known Action {action.value!r} not processed yet, "
                    f"skipped",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            quantity_decimal = Decimal((row["Quantity"] or "0").strip())
            if quantity_decimal != quantity_decimal.to_integral_value():
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"fractional Quantity {quantity_decimal} "
                    f"not supported for this provider, skipped",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            price = parse_money(row["Price"])
            amount = parse_money(row["Amount"])
            quantity = int(quantity_decimal)
            if abs(quantity * price - amount) > Decimal("0.01"):
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"Quantity x Price ({quantity * price}) "
                    f"does not match Amount ({amount})",
                    file=sys.stderr,
                )
            events.append(
                Event(
                    date=parse_date(row["Date"], provider.date_format),
                    action=action,
                    symbol=(row["Symbol"] or "").strip(),
                    description=(row["Description"] or "").strip(),
                    quantity=quantity,
                    price=price,
                    fees=parse_money(row.get("Fees & Comm")),
                    amount=amount,
                    currency=provider.currency,
                    provider=provider.name,
                    source_file=path.name,
                    row=reader.line_num,
                )
            )
    return events, skipped


def find_inputs(data_dir):
    """Collect CSVs, one provider folder at a time.

    config.json files are skipped silently. Other non-CSV files
    warn and are skipped, they do not stop the run.
    """
    input_dir = data_dir / "input"
    if not input_dir.is_dir():
        print(f"Error: input dir not found: {input_dir}", file=sys.stderr)
        return None

    inputs = []
    for entry in sorted(input_dir.iterdir()):
        if entry.is_file() and not entry.name.startswith("."):
            print(
                f"Warning: {entry.name} directly under input/, "
                f"expected a provider folder, skipped",
                file=sys.stderr,
            )

    provider_dirs = [
        p
        for p in sorted(input_dir.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    ]
    for provider_dir in provider_dirs:
        config_path = provider_dir / "config.json"
        if not config_path.is_file():
            print(
                f"Warning: provider {provider_dir.name}: "
                f"no config.json, provider skipped",
                file=sys.stderr,
            )
            continue
        provider = load_provider_config(provider_dir)
        if provider is None:
            continue
        files = sorted(
            p
            for p in provider_dir.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
        for path in files:
            if path.name == "config.json":
                continue  # config, not data, no warning
            if path.suffix.lower() == ".csv":
                inputs.append(ProviderInput(path=path, provider=provider))
            else:
                print(
                    f"Warning: {path} is not a CSV or config file, skipped",
                    file=sys.stderr,
                )

    if not inputs:
        print(f"Error: no CSV found under {input_dir}", file=sys.stderr)
        return None
    return inputs


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cgt")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir) if args.output_dir else data_dir / "output"
    cache_dir = output_dir / "cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.is_dir():
        print(f"Error: data dir not found: {data_dir}", file=sys.stderr)
        return 2

    inputs = find_inputs(data_dir)
    if inputs is None:
        return 2

    all_events = []
    total_skipped = 0
    for inp in inputs:
        events, skipped = parse_csv(inp.path, inp.provider)
        all_events.extend(events)
        total_skipped += skipped

    for event in all_events:
        print(event)
    print(
        f"Parsed {len(all_events)} Sell event(s), "
        f"skipped {total_skipped}, from {len(inputs)} file(s)."
    )
    print(f"Data dir: {data_dir}, output dir: {output_dir}, cache dir: {cache_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
