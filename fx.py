"""FX conversion: ParsedEvent to Event, stub for now.

Stub only: every event gets original_units_per_gbp = 1,
fx_source = "stub", so GBP amounts are unchanged and
non-GBP amounts are used as given, exactly as before
this refactor. This is a structure change only, not a
real FX change, so output must be identical to before.

No grouping here. Events are converted one by one, in
order. When real rates land, each event will look up
its month in the FX cache, one by one, and the cache
itself handles reuse, so grouping by month is not needed.

cache_dir is accepted now so main.py does not have to
change when real FX lands. It is unused by the stub.
TODO: replace the stub rate with a per-event cache
lookup: GBP -> 1 / "GBP", otherwise the cached HMRC
monthly rate for that event's month and currency,
screaming if the month or currency is missing.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from models import Event, ParsedEvent, Pricing


def convert_events(parsed_events: list[ParsedEvent], cache_dir: Path) -> list[Event]:
    # cache_dir is unused in the stub, kept for the real version.
    _ = cache_dir
    events: list[Event] = []
    for parsed in parsed_events:
        pricing = Pricing(
            original_currency=parsed.original_currency,
            original_price=parsed.original_price,
            original_amount=parsed.original_amount,
            original_fees=parsed.original_fees,
            original_units_per_gbp=Decimal(1),
            fx_source="stub",
        )
        events.append(
            Event(
                date=parsed.date,
                action=parsed.action,
                symbol=parsed.symbol,
                description=parsed.description,
                quantity=parsed.quantity,
                pricing=pricing,
                provider=parsed.provider,
                source_file=parsed.source_file,
                row=parsed.row,
            )
        )
    return events
