#!/usr/bin/env python3
"""First step: parse a provider CSV into internal events.

Layout, with --data-dir defaulting to "data":
  <data-dir>/input/    provider CSVs, any filename, CSV only
  <data-dir>/output/   reports (later steps)
  <data-dir>/output/cache/  internal cache, not a CLI option
"""

import argparse
import csv
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
    name: str
    date_format: str
    currency: Currency


EXAMPLE_PROVIDER = ProviderConfig(
    name="test US provider",
    date_format="%m/%d/%Y",
    currency=Currency.USD,
)


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
                    source_file=path.name,
                    row=reader.line_num,
                )
            )
    return events, skipped


def find_inputs(data_dir):
    """All CSVs under <data-dir>/input/, any filename.

    Any non-CSV file there is an error: it means a wrong or
    unsupported file was placed in the input folder, and silently
    skipping it could hide real data.
    """
    input_dir = data_dir / "input"
    if not input_dir.is_dir():
        print(f"Error: input dir not found: {input_dir}", file=sys.stderr)
        return None
    files = [
        p
        for p in sorted(input_dir.iterdir())
        if p.is_file() and not p.name.startswith(".")
    ]
    non_csv = [p for p in files if p.suffix.lower() != ".csv"]
    if non_csv:
        names = ", ".join(p.name for p in non_csv)
        print(
            f"Error: non-CSV file(s) in {input_dir}: {names}",
            file=sys.stderr,
        )
        return None
    csv_files = [p for p in files if p.suffix.lower() == ".csv"]
    if not csv_files:
        print(f"Error: no CSV found in {input_dir}", file=sys.stderr)
        return None
    return csv_files


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
    for path in inputs:
        events, skipped = parse_csv(path, EXAMPLE_PROVIDER)
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
