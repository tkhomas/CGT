"""Vesting date correction: assemble real buys from lapses.

The equity file dates a vest on the scheduled day, but
when that day is not a business day the shares vest on
the next business day, and the personal file's Stock Plan
Activity row says when -- its "as of" date. The equity
parser emits lapses, not buys, so a vesting and a genuine
buy can never be confused, even on the same day. This
join turns each lapse into the buy it is, with the date
corrected to the vesting record's date.

The two files are matched by (symbol, year, month). The
record carries the shares that vested, the lapse the
shares deposited: they differ when shares were withheld,
so the quantities are not a check. A record with no lapse
is dropped, and a lapse with no record keeps the scheduled
date; both are warned about, not failures.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import replace

from models import Action, ParsedEvent


def correct_vest_dates(events: list[ParsedEvent]) -> list[ParsedEvent]:
    """Turn lapses into buys, with dates corrected from Stock Plan Activity rows."""
    vestings = [e for e in events if e.action is Action.STOCK_PLAN_ACTIVITY]
    lapses = [e for e in events if e.action is Action.LAPSE]
    if not lapses and not vestings:
        return list(events)
    if not vestings:
        assembled = {id(lapse): replace(lapse, action=Action.BUY) for lapse in lapses}
        return [assembled.get(id(event), event) for event in events]
    by_key: dict[tuple[str, int, int], list[ParsedEvent]] = defaultdict(list)
    for vesting in vestings:
        by_key[(vesting.symbol, vesting.date.year, vesting.date.month)].append(vesting)
    lapse_by_key: dict[tuple[str, int, int], list[ParsedEvent]] = defaultdict(list)
    for lapse in lapses:
        lapse_by_key[(lapse.symbol, lapse.date.year, lapse.date.month)].append(lapse)
    assembled: dict[int, ParsedEvent] = {}
    dropped: set[int] = set()
    for key in set(by_key) | set(lapse_by_key):
        symbol, year, month = key
        group_v = sorted(by_key.get(key, []), key=lambda e: (e.quantity, e.date))
        group_l = sorted(lapse_by_key.get(key, []), key=lambda e: (e.quantity, e.date))
        by_qty_v: dict[int, list[ParsedEvent]] = defaultdict(list)
        for vesting in group_v:
            by_qty_v[vesting.quantity].append(vesting)
        by_qty_l: dict[int, list[ParsedEvent]] = defaultdict(list)
        for lapse in group_l:
            by_qty_l[lapse.quantity].append(lapse)
        unmatched_v: list[ParsedEvent] = []
        unmatched_l: list[ParsedEvent] = []
        for qty in set(by_qty_v) | set(by_qty_l):
            vs = by_qty_v.get(qty, [])
            ls = by_qty_l.get(qty, [])
            for vesting, lapse in zip(vs, ls):
                assembled[id(lapse)] = replace(
                    lapse, action=Action.BUY, date=vesting.date
                )
            unmatched_v.extend(vs[len(ls) :])
            unmatched_l.extend(ls[len(vs) :])
        unmatched_v.sort(key=lambda e: e.date)
        unmatched_l.sort(key=lambda e: e.date)
        for vesting, lapse in zip(unmatched_v, unmatched_l):
            assembled[id(lapse)] = replace(lapse, action=Action.BUY, date=vesting.date)
        for vesting in unmatched_v[len(unmatched_l) :]:
            print(
                f"Warning: stock plan record "
                f"{vesting.source_file}:{vesting.row} for "
                f"{symbol} {year}-{month:02d} has no vesting "
                f"lapse, dropped",
                file=sys.stderr,
            )
            dropped.add(id(vesting))
        for lapse in unmatched_l[len(unmatched_v) :]:
            print(
                f"Warning: vesting lapse "
                f"{lapse.source_file}:{lapse.row} for {symbol} "
                f"{year}-{month:02d} has no stock plan record, "
                f"kept with the scheduled date",
                file=sys.stderr,
            )
            assembled[id(lapse)] = replace(lapse, action=Action.BUY)
    return [
        assembled.get(id(event), event)
        for event in events
        if event.action is not Action.STOCK_PLAN_ACTIVITY and id(event) not in dropped
    ]
