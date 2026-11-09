"""Helpers shared by every parser.

Only what is genuinely format-independent lives here.
Anything tied to one file's shape -- the "as of" date in
the transactions file, the two-row lapse in the vesting
file -- lives in that format's own module.
"""

from __future__ import annotations

from decimal import Decimal


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
