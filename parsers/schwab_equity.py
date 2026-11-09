"""Schwab equity awards (vesting) CSV: one event over two rows, into ParsedEvents.

One lapse is two physical rows: the first carries the
date, the action, the symbol and the quantity vested; the
following row, with an empty date, carries the award
details, including the fair market value price. The amount
is not in the file: it is quantity times that price, so a
lapse is exactly a buy of that many shares at that price.
Everything about tax -- the tax withheld, the shares sold
or withheld for it -- is ignored: that tax is already paid
through income tax.
"""

from __future__ import annotations

import csv
import io
import sys
from decimal import Decimal
from pathlib import Path

from models import Action, ParsedEvent, ProviderConfig
from parsers.common import parse_date, parse_money

_ACTION: dict[str, Action] = {"lapse": Action.BUY}


def _parse_line(text: str) -> list[str]:
    return next(csv.reader(io.StringIO(text)))


def _logical_rows(path: Path) -> list[tuple[dict[str, str], int]]:
    """Read the file as logical records, one per event half.

    A record is one physical line, unless it has fewer
    fields than the header: then the next line is joined
    in, and so on, until the field count matches. A record
    that ends up with more fields than the header is a
    malformed file, not a guessable one: it raises
    ValueError, loud, for the user to deal with. A record
    that runs out of lines while still short raises the
    same way.
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is None:
        return []
    header = _parse_line(lines[first])
    expected = len(header)
    rows = []
    i = first + 1
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        line_no = i + 1
        row = _parse_line(lines[i])
        i += 1
        while len(row) < expected and i < len(lines):
            if not lines[i].strip():
                i += 1
                continue
            row = row + _parse_line(lines[i])
            i += 1
        if len(row) > expected:
            raise ValueError(
                f"{path}:{line_no}: expected {expected} columns, "
                f"got {len(row)} -- fix the input file"
            )
        if len(row) < expected:
            raise ValueError(
                f"{path}:{line_no}: only {len(row)} of {expected} "
                f"columns, even after joining the following lines "
                f"-- fix the input file"
            )
        rows.append((dict(zip(header, row)), line_no))
    return rows


def _make_event(
    header_row: dict[str, str],
    detail_row: dict[str, str] | None,
    line_no: int,
    path: Path,
    provider: ProviderConfig,
) -> ParsedEvent | None:
    action = _ACTION.get((header_row.get("Action") or "").strip().lower())
    if action is None:
        print(
            f"Warning: {path.name}:{line_no}: "
            f"unrecognised Action {header_row.get('Action')!r}, skipped",
            file=sys.stderr,
        )
        return None

    quantity_decimal = Decimal((header_row.get("Quantity") or "0").strip())
    if quantity_decimal != quantity_decimal.to_integral_value():
        print(
            f"Warning: {path.name}:{line_no}: "
            f"fractional Quantity {quantity_decimal} "
            f"not supported for this provider, skipped",
            file=sys.stderr,
        )
        return None
    quantity = int(quantity_decimal)
    if quantity <= 0:
        print(
            f"Warning: {path.name}:{line_no}: "
            f"quantity {quantity_decimal} not positive, skipped",
            file=sys.stderr,
        )
        return None

    # The price lives on the detail row; a lapse whose own
    # row already carries it is complete as it stands.
    price_row = detail_row if detail_row is not None else header_row
    price = parse_money(price_row.get("FairMarketValuePrice"))
    if price <= 0:
        print(
            f"Warning: {path.name}:{line_no}: no fair market value price, skipped",
            file=sys.stderr,
        )
        return None

    return ParsedEvent(
        date=parse_date(header_row["Date"], provider.date_format),
        action=action,
        symbol=(header_row.get("Symbol") or "").strip(),
        description=(header_row.get("Description") or "").strip(),
        quantity=quantity,
        original_currency=provider.currency,
        original_price=price,
        original_amount=quantity * price,
        original_fees=parse_money(header_row.get("FeesAndCommissions")),
        provider=provider.name,
        source_file=path.name,
        row=line_no,
    )


def parse_csv(path: Path, provider: ProviderConfig) -> tuple[list[ParsedEvent], int]:
    events = []
    skipped = 0
    pending: tuple[dict[str, str], int] | None = None
    for row, line_no in _logical_rows(path):
        if (row.get("Date") or "").strip():
            # A dated row starts a lapse. The previous one,
            # if it is still waiting, never got its detail
            # row, so it cannot be priced.
            if pending is not None:
                _, header_no = pending
                print(
                    f"Warning: {path.name}:{header_no}: "
                    f"lapse has no following detail row, skipped",
                    file=sys.stderr,
                )
                skipped += 1
                pending = None
            if (row.get("FairMarketValuePrice") or "").strip():
                # Complete as it stands: the price is on
                # this row, so no detail row is needed.
                event = _make_event(row, None, line_no, path, provider)
            else:
                pending = (row, line_no)
                continue
            if event is None:
                skipped += 1
            else:
                events.append(event)
            continue
        if pending is None:
            print(
                f"Warning: {path.name}:{line_no}: "
                f"detail row with no lapse before it, skipped",
                file=sys.stderr,
            )
            skipped += 1
            continue
        header_row, header_no = pending
        pending = None
        event = _make_event(header_row, row, header_no, path, provider)
        if event is None:
            skipped += 1
        else:
            events.append(event)
    if pending is not None:
        _, header_no = pending
        print(
            f"Warning: {path.name}:{header_no}: "
            f"lapse has no following detail row, skipped",
            file=sys.stderr,
        )
        skipped += 1
    return events, skipped
