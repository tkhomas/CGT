"""Vesting date correction: assemble real buys from lapses.

The equity file dates a vest on the scheduled day, but
when that day is not a business day the shares vest on
the next business day, and the personal file's Stock Plan
Activity row says when -- its "as of" date. The equity
parser does not emit buys: it emits lapses, so a vesting
and a genuine buy can never be confused, even on the
same day. This join turns each lapse into the buy it is,
with the date corrected to the vesting record's date, so
a sell on the vest date is a same-day disposal, not a
pool one, and the gain is the vest value against the sale
price. The lapse keeps its price, quantity and fees;
only its date moves, and only when a record says so.

The two files are matched without knowing they are the
same provider: a vesting record and a lapse pair up by
(symbol, year, month), in date order within the month,
and their quantities must agree. Any mismatch -- a
vesting record with no lapse, a lapse with no record,
or quantities that differ -- fails the run, loud, rather
than guessing a date. A genuine buy is never a lapse, so
it is never touched and never steals a pairing. When
there are no vesting records at all, the lapses become
buys as they are: the equity dates stand, as before.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from models import Action, ParsedEvent


def correct_vest_dates(events: list[ParsedEvent]) -> list[ParsedEvent]:
    """Turn lapses into buys, with dates corrected from Stock Plan Activity rows.

    Takes all parsed events, including the lapses and the
    Stock Plan Activity records, and returns them with
    the records removed and each lapse replaced by the
    buy it is. Every other event is returned untouched,
    in order. A mismatch raises ValueError: the run stops,
    it does not fall back to the scheduled date.
    """
    vestings = [e for e in events if e.action is Action.STOCK_PLAN_ACTIVITY]
    lapses = [e for e in events if e.action is Action.LAPSE]
    if not lapses and not vestings:
        return list(events)
    if not vestings:
        # No records at all: the equity dates stand. Each
        # lapse becomes a buy as it is.
        assembled = {id(lapse): replace(lapse, action=Action.BUY) for lapse in lapses}
        return [assembled.get(id(event), event) for event in events]
    by_key: dict[tuple[str, int, int], list[ParsedEvent]] = defaultdict(list)
    for vesting in vestings:
        by_key[(vesting.symbol, vesting.date.year, vesting.date.month)].append(vesting)
    lapse_by_key: dict[tuple[str, int, int], list[ParsedEvent]] = defaultdict(list)
    for lapse in lapses:
        lapse_by_key[(lapse.symbol, lapse.date.year, lapse.date.month)].append(lapse)
    assembled: dict[int, ParsedEvent] = {}
    for key in set(by_key) | set(lapse_by_key):
        symbol, year, month = key
        group_v = sorted(by_key.get(key, []), key=lambda e: e.date)
        group_l = sorted(lapse_by_key.get(key, []), key=lambda e: e.date)
        if len(group_v) != len(group_l):
            raise ValueError(
                f"cannot match vesting dates for {symbol} "
                f"{year}-{month:02d}: {len(group_v)} stock plan "
                f"record(s) but {len(group_l)} vesting lapse(s) "
                "-- fix the input files"
            )
        for vesting, lapse in zip(group_v, group_l):
            if vesting.quantity != lapse.quantity:
                raise ValueError(
                    f"cannot match vesting dates for {symbol} "
                    f"{year}-{month:02d}: stock plan record "
                    f"{vesting.source_file}:{vesting.row} has "
                    f"{vesting.quantity} shares but the vesting "
                    f"lapse {lapse.source_file}:{lapse.row} has "
                    f"{lapse.quantity} -- fix the input files"
                )
            assembled[id(lapse)] = replace(lapse, action=Action.BUY, date=vesting.date)
    return [
        assembled.get(id(event), event)
        for event in events
        if event.action is not Action.STOCK_PLAN_ACTIVITY
    ]
