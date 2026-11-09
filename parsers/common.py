"""Helpers shared by every parser.

Only what is genuinely format-independent lives here.
Anything tied to one file's shape -- the two-row lapse
in the equity file, the income aliases in the personal
file -- lives in that format's own module.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal


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
