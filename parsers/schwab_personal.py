"""Schwab personal brokerage transactions CSV: one row per event, into ParsedEvents.

The events come out in the order they happened: the file
order is checked, not assumed -- chronological files are
kept as they are, newest-first files are reversed, and a
file that runs neither way is sorted by date, with a
warning (see order_events in parsers/common.py).
"""

from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path

from models import IGNORED_ACTIONS, INCOME_ACTIONS, Action, ParsedEvent, ProviderConfig
from parsers.common import (
    order_events,
    parse_dates,
    parse_int,
    parse_money,
)

# Schwab names for the same canonical action. Qualified
# versus non-qualified is a US distinction, immaterial for
# UK tax, so both map to one Dividend action.
_ACTION_ALIASES: dict[str, Action] = {
    "qualified dividend": Action.DIVIDEND,
    "non-qualified div": Action.DIVIDEND,
    "pr yr non-qual div": Action.DIVIDEND,
    "nra tax adj": Action.WITHHOLDING_TAX,
    "foreign tax paid": Action.WITHHOLDING_TAX,
    "pr yr nra tax": Action.WITHHOLDING_TAX,
    "credit interest": Action.INTEREST,
    "bond interest": Action.INTEREST,
}


def parse_action(text: str | None, source_file: str, row: int) -> Action | None:
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

            # Stock plan activity is not a CGT event, but
            # it is not ignored either: it says when the
            # shares actually vested -- its "as of" date,
            # when the scheduled vest day was not a business
            # day -- and how many landed. It becomes a
            # date-correction record, not an event: the
            # equity file's lapse keeps its price, but its
            # date is corrected to this one (see
            # parsers/schwab_vesting.py), and this record is dropped
            # there. No price here: that is what the equity
            # file is for.
            if action is Action.STOCK_PLAN_ACTIVITY:
                try:
                    vest_quantity = parse_int(row["Quantity"], "Quantity")
                except ValueError as exc:
                    print(
                        f"Warning: {path.name}:{reader.line_num}: {exc}, skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue
                if vest_quantity <= 0:
                    print(
                        f"Warning: {path.name}:{reader.line_num}: "
                        f"quantity {vest_quantity} not positive, skipped",
                        file=sys.stderr,
                    )
                    skipped += 1
                    continue
                # A vesting is acquired on the date it is
                # effective as of, not the day it was
                # posted. Without " as of ", the posted
                # date is the vest date.
                posted, as_of = parse_dates(row["Date"], provider.date_format)
                vest_date = as_of if as_of is not None else posted
                events.append(
                    ParsedEvent(
                        date=vest_date,
                        action=action,
                        symbol=(row["Symbol"] or "").strip(),
                        description=(row["Description"] or "").strip(),
                        quantity=vest_quantity,
                        original_currency=provider.currency,
                        original_price=Decimal(0),
                        original_amount=Decimal(0),
                        original_fees=Decimal(0),
                        provider=provider.name,
                        source_file=path.name,
                        row=reader.line_num,
                    )
                )
                continue

            # Recognised but not CGT events: a wire sent
            # is cash out. Dropped here, silently, so
            # they never reach the FX converter, the
            # timeline, or the reports.
            if action in IGNORED_ACTIONS:
                continue

            # Income rows have no quantity and no price:
            # the Amount is the value itself, positive for
            # income, negative for tax already withheld.
            # Recorded as written; the trade checks below
            # are for buys and sells only. An income row
            # takes the posted date: the "as of" date, if
            # any, is not its date.
            posted, _ = parse_dates(row["Date"], provider.date_format)
            if action in INCOME_ACTIONS:
                events.append(
                    ParsedEvent(
                        date=posted,
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

            # A quantity that is not a whole number is a bad
            # row, warned about and skipped like the other
            # bad rows, not a reason to kill the whole run.
            try:
                quantity = parse_int(row["Quantity"], "Quantity")
            except ValueError as exc:
                print(
                    f"Warning: {path.name}:{reader.line_num}: {exc}, skipped",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            price = parse_money(row["Price"])
            fees = parse_money(row.get("Fees & Comm"))
            # Schwab reports the Amount net of fees: a buy's
            # Amount is money out, quantity x price plus fees,
            # a sell's is money in, quantity x price minus
            # fees. The Amount is the source of truth -- the
            # money that actually moved -- so it is stored as
            # written. Price, quantity and fees are only a
            # check that the row adds up, never the value.
            amount = abs(parse_money(row["Amount"]))
            if quantity <= 0:
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"quantity {quantity} not positive, skipped",
                    file=sys.stderr,
                )
                skipped += 1
                continue

            # The price is given to four decimals and the amount
            # to cents, so only the amount's own rounding is
            # left: a fixed 0.01 covers it. For a buy the cash
            # out is the trade value plus fees, for a sell the
            # cash in is the trade value minus fees, so the
            # difference between the Amount and quantity x price
            # is the fees, either way.
            if action is Action.BUY:
                difference = amount - quantity * price
            else:
                difference = quantity * price - amount
            if abs(difference - fees) > Decimal("0.01"):
                # The Amount is the source of truth, so the
                # row is kept with its value; the warning is
                # the only marker that the price does not add
                # up, as it does not for a bond, whose price
                # is quoted as a percent of par.
                print(
                    f"Warning: {path.name}:{reader.line_num}: "
                    f"Quantity x Price ({quantity * price}) "
                    f"does not match Amount ({amount})",
                    file=sys.stderr,
                )
            events.append(
                ParsedEvent(
                    date=posted,
                    action=action,
                    symbol=(row["Symbol"] or "").strip(),
                    description=(row["Description"] or "").strip(),
                    quantity=quantity,
                    original_currency=provider.currency,
                    original_price=price,
                    original_amount=amount,
                    original_fees=fees,
                    provider=provider.name,
                    source_file=path.name,
                    row=reader.line_num,
                )
            )
    # The file order is checked, not assumed: chronological
    # files are kept, newest-first files are reversed, and
    # a file that runs neither way is sorted by date, with
    # a warning. The timeline takes input order as the order
    # the events happened, so the events come out oldest
    # first.
    return order_events(events, path.name), skipped
