"""CSV row parsing into ParsedEvents."""

from __future__ import annotations

import csv
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from models import Action, ParsedEvent, ProviderConfig


def parse_date(text: str, date_format: str) -> date:
    # Returns a native date object, not a string. Internally the
    # date is unambiguous; format it as ISO YYYY-MM-DD only on output.
    return datetime.strptime(text.strip(), date_format).date()


def parse_action(text: str | None, source_file: str, row: int) -> Action | None:
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


def parse_money(text: str | None) -> Decimal:
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


def parse_csv(path: Path, provider: ProviderConfig) -> tuple[list[ParsedEvent], int]:
    events = []
    skipped = 0
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            action = parse_action(row.get("Action"), path.name, reader.line_num)
            if action is None:
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
            if quantity_decimal <= 0:
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"quantity {quantity_decimal} not positive, skipped",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            if abs(quantity * price - amount) > Decimal("0.01"):
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"Quantity x Price ({quantity * price}) "
                    f"does not match Amount ({amount})",
                    file=sys.stderr,
                )
            events.append(
                ParsedEvent(
                    date=parse_date(row["Date"], provider.date_format),
                    action=action,
                    symbol=(row["Symbol"] or "").strip(),
                    description=(row["Description"] or "").strip(),
                    quantity=quantity,
                    original_currency=provider.currency,
                    original_price=price,
                    original_amount=amount,
                    original_fees=parse_money(row.get("Fees & Comm")),
                    provider=provider.name,
                    source_file=path.name,
                    row=reader.line_num,
                )
            )
    return events, skipped
