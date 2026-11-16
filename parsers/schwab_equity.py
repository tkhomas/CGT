"""Schwab equity awards (vesting) CSV: one event over two rows, into ParsedEvents.

One lapse is two physical rows: the first carries the
date, the action, the symbol and the quantity vested; the
following row, with an empty date, carries the award
details, including the fair market value price and the
shares actually deposited. The amount is not in the file:
it is the deposited shares times that price, plus the
fees, so a lapse is exactly a buy of that many shares at
that price. The shares sold or withheld for tax never
reach the account -- that tax is settled through payroll,
not here -- so they are out of the picture entirely, and
the tax dollars are ignored too. The file should agree
with itself: the vested shares less the withheld shares
are the deposited shares, and a row where they do not
agree warns. The events come out in the order they
happened: the file order is checked, not assumed --
chronological files are kept as they are, newest-first
files are reversed, and a file that runs neither way is
sorted by date, with a warning (see order_events in
parsers/common.py).
"""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

from models import Action, ParsedEvent, ProviderConfig
from parsers.common import order_events, parse_date, parse_int, parse_money

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

    # The price and the deposited shares live on the
    # detail row; a lapse whose own row already carries
    # them is complete as it stands.
    price_row = detail_row if detail_row is not None else header_row
    price = parse_money(price_row.get("FairMarketValuePrice"))
    if price <= 0:
        print(
            f"Warning: {path.name}:{line_no}: no fair market value price, skipped",
            file=sys.stderr,
        )
        return None

    # The buy is the shares that actually reach the
    # account, not the shares that vested: the shares sold
    # or withheld for tax are settled through payroll and
    # never land here. A figure that is not a whole number
    # is a bad row, warned about and skipped like the
    # other bad rows, not a reason to kill the whole run.
    try:
        quantity = parse_int(price_row.get("NetSharesDeposited"), "NetSharesDeposited")
    except ValueError as exc:
        print(
            f"Warning: {path.name}:{line_no}: {exc}, skipped",
            file=sys.stderr,
        )
        return None
    if quantity <= 0:
        print(
            f"Warning: {path.name}:{line_no}: "
            f"NetSharesDeposited {quantity} not positive, skipped",
            file=sys.stderr,
        )
        return None

    # The file should agree with itself: the vested shares
    # less the shares sold or withheld for tax are the
    # deposited shares. If it does not, warn, but keep the
    # row -- the deposited figure is what the account
    # holds, so it is the one the buy is built on. A
    # figure that cannot be read means the check cannot
    # run, not that the row is bad.
    try:
        vested = parse_int(header_row.get("Quantity"), "Quantity")
        withheld = parse_int(
            price_row.get("SharesSoldWithheldForTaxes"),
            "SharesSoldWithheldForTaxes",
        )
    except ValueError:
        vested = None
        withheld = None
    if vested is not None and withheld is not None and vested - withheld != quantity:
        print(
            f"Warning: {path.name}:{line_no}: "
            f"Quantity {vested} less {withheld} withheld "
            f"is {vested - withheld}, not NetSharesDeposited "
            f"{quantity} -- kept the deposited figure",
            file=sys.stderr,
        )

    # The amount is the money out: the deposited shares at
    # fair market value plus the fees. The matcher uses the
    # amount as it stands, so the fees belong in it here,
    # not added later.
    fees = parse_money(header_row.get("FeesAndCommissions"))
    return ParsedEvent(
        date=parse_date(header_row["Date"], provider.date_format),
        action=action,
        symbol=(header_row.get("Symbol") or "").strip(),
        description=(header_row.get("Description") or "").strip(),
        quantity=quantity,
        original_currency=provider.currency,
        original_price=price,
        original_amount=quantity * price + fees,
        original_fees=fees,
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
    # The file order is checked, not assumed: chronological
    # files are kept, newest-first files are reversed, and
    # a file that runs neither way is sorted by date, with
    # a warning. The timeline takes input order as the order
    # the events happened, so the events come out oldest
    # first.
    return order_events(events, path.name), skipped
