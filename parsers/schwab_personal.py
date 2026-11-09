"""Schwab personal brokerage transactions CSV: one row per event, into ParsedEvents."""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

from models import IGNORED_ACTIONS, INCOME_ACTIONS, Action, ParsedEvent, ProviderConfig
from parsers.common import parse_date, parse_money

# Schwab uses several names for the same thing: a dividend
# is "Qualified Dividend" or "Pr Yr Non-Qual Div", and tax
# already paid is "NRA Tax Adj", "Foreign Tax Paid" or
# "Pr Yr NRA Tax". The alias is provider vocabulary; the
# Action it maps to is canonical. Qualified versus
# non-qualified is a US distinction, immaterial for UK
# tax, so both map to one Dividend action. The tax names
# map to Withholding Tax, the only tax action: a tax row
# does not say whether it is tax on a dividend or on
# interest, and reporting decides that from the dividend
# row with the same symbol on the same date.
_ACTION_ALIASES: dict[str, Action] = {
    "qualified dividend": Action.DIVIDEND,
    "pr yr non-qual div": Action.DIVIDEND,
    "nra tax adj": Action.WITHHOLDING_TAX,
    "foreign tax paid": Action.WITHHOLDING_TAX,
    "pr yr nra tax": Action.WITHHOLDING_TAX,
    "credit interest": Action.INTEREST,
    "bond interest": Action.INTEREST,
}


def parse_action(text: str | None, source_file: str, row: int) -> Action | None:
    # Returns an Action, or None for an unrecognised action.
    # Unrecognised actions warn at parse time and are skipped by
    # the caller, never silently dropped.
    key = (text or "").strip().lower()
    if key in _ACTION_ALIASES:
        return _ACTION_ALIASES[key]
    for action in Action:
        if action.value == key:
            return action
    print(
        f"Warning: {source_file}:{row}: unrecognised Action {text!r}, skipped",
        file=sys.stderr,
    )
    return None


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

            # Ignored actions are recognised, not
            # unrecognised, but they are not CGT events:
            # a wire sent is cash out, a stock plan
            # activity is drawn from a different report.
            # Drop them here, silently, so they never
            # reach the FX converter, the timeline, or
            # the reports.
            if action in IGNORED_ACTIONS:
                continue

            # Income rows have no quantity and no price:
            # the Amount is the value itself, positive for
            # a dividend or interest, negative for tax
            # already withheld. Record the event as written
            # and stop here; the trade checks below are
            # for buys and sells only.
            if action in INCOME_ACTIONS:
                events.append(
                    ParsedEvent(
                        date=parse_date(row["Date"], provider.date_format),
                        action=action,
                        symbol=(row["Symbol"] or "").strip(),
                        description=(row["Description"] or "").strip(),
                        quantity=0,
                        original_currency=provider.currency,
                        original_price=Decimal(0),
                        original_amount=parse_money(row["Amount"]),
                        original_fees=parse_money(row.get("Fees & Comm")),
                        provider=provider.name,
                        source_file=path.name,
                        row=reader.line_num,
                    )
                )
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
