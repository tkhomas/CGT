"""Helpers shared by every parser.

Only what is genuinely format-independent lives here.
Anything tied to one file's shape -- the two-row lapse
in the equity file, the income aliases in the personal
file -- lives in that format's own module.
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from models import ParsedEvent


def parse_date(text: str, date_format: str) -> date:
    # Schwab can show two dates, e.g. "08/18/2026 as of
    # 08/17/2026": the first date is the transaction date,
    # the second is the date it is reported as of. Take the
    # first. For a lapse that first date is the vest date,
    # which is the acquisition date.
    cleaned = text.strip().lower()
    marker = " as of "
    if marker in cleaned:
        cleaned = cleaned[: cleaned.index(marker)].strip()
    return datetime.strptime(cleaned, date_format).date()


def parse_money(text: str | None) -> Decimal:
    # Strip currency symbols and thousands separators. This is
    # formatting cleanup only: it does NOT decide the currency.
    # "$" alone is ambiguous (USD, MXN, AUD and others all use it),
    # so the currency always comes from the provider config.
    if text is None or text.strip() == "":
        return Decimal(0)
    cleaned = text.strip()
    for symbol in ("$", "£", "€", "¥", "₹"):
        cleaned = cleaned.replace(symbol, "")
    return Decimal(cleaned.replace(",", "").strip())


def parse_int(text: str | None, field: str) -> int:
    """Parse a whole number of shares from a field that holds one.

    Strips thousands separators. A blank field parses as 0:
    the caller decides whether 0 is allowed. Anything that
    is not a whole number raises ValueError naming the field
    and the problem, so the caller can warn about the row
    or treat it as unreadable.
    """
    raw = text if text is not None else ""
    try:
        value = Decimal(raw.replace(",", "").strip() or "0")
    except InvalidOperation:
        raise ValueError(f"{field} {text!r} is not a number") from None
    if value != value.to_integral_value():
        raise ValueError(
            f"fractional {field} {value} not supported for this provider"
        ) from None
    return int(value)


def order_events(events: list[ParsedEvent], source_file: str) -> list[ParsedEvent]:
    """Put a file's events in the order they happened.

    The file order is checked, not assumed. A file whose
    dates run oldest first is chronological and is kept as
    it is; a file whose dates run newest first is reversed.
    A file with more than one date that runs neither way
    has no order to trust: the events are sorted by date,
    and the warning says so -- within a single day the
    order is then as written in the file, which may not be
    the order the events happened. A file that is all one
    date cannot be checked at all: the events are taken in
    file order, as if chronological, and the warning says
    so. A single event has no order to determine.
    """
    if len(events) < 2:
        return events
    dates = [event.date for event in events]
    if all(d == dates[0] for d in dates):
        print(
            f"Warning: {source_file}: all {len(events)} events are on "
            f"{dates[0].isoformat()}, so the file order cannot be "
            "checked against the dates -- the events are taken in "
            "file order, as if chronological.",
            file=sys.stderr,
        )
        return events
    if all(dates[i] <= dates[i + 1] for i in range(len(dates) - 1)):
        return events
    if all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1)):
        return list(reversed(events))
    print(
        f"Warning: {source_file}: events are not in date order -- "
        "neither chronological nor reverse chronological.\n"
        "    Events have been sorted by date, but within a single day "
        "the order is as written in the file and may not be the order "
        "they happened, so same-day matching may be wrong.",
        file=sys.stderr,
    )
    return sorted(events, key=lambda event: event.date)
