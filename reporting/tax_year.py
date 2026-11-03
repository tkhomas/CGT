"""UK tax-year grouping, for reporting only.

A tax year runs 6 April to 5 April. A disposal belongs to
the tax year of its sell date, not its buy date.

No pausing in the main loop, and no change to matching:
the matcher runs the full timeline first, so a sale near
year end is matched exactly as it should be, even if the
matching buy falls in the next tax year. Grouping happens
afterwards, from the finished disposals and timeline.

That is what makes bed-and-breakfast across year end work:
a sale on 5 April matched by a buy on 7 April is a disposal
of the earlier year, with the later buy as its cost. Its
buy therefore appears in the earlier year's events, for
audit, even though its date is after that year's end.
It is not counted in that year's holdings, which are at
5 April.

A tax year is final only once the report is produced more
than 30 days past its end. Until then it is _CURRENT: a
sale in its last 30 days could still be matched by a buy
that has not happened yet, and, if it is the latest year,
more events can still occur in it.

Finality is judged against the production date, not the
last event date. The last event date is not the end of
the world: there may simply have been no transactions
since then, and a year whose bed-and-breakfast window
closed months ago in real time is final even if no later
transaction happens to exist in the data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from matching import Disposal, MatchType
from models import Action, Event

BED_AND_BREAKFAST_DAYS = 30


@dataclass(frozen=True)
class TaxYear:
    start_year: int

    @property
    def end_year(self) -> int:
        return self.start_year + 1

    @property
    def start(self) -> date:
        return date(self.start_year, 4, 6)

    @property
    def end(self) -> date:
        return date(self.end_year, 4, 5)

    @property
    def label(self) -> str:
        # 2024/25, for display.
        return f"{self.start_year}/{str(self.end_year)[-2:]}"

    @property
    def file_label(self) -> str:
        # 2024-25, for file names: no slash.
        return f"{self.start_year}-{str(self.end_year)[-2:]}"


def tax_year_of(d: date) -> TaxYear:
    if (d.month, d.day) >= (4, 6):
        return TaxYear(d.year)
    return TaxYear(d.year - 1)


def tax_years_for(timeline: list[Event], disposals: list[Disposal]) -> list[TaxYear]:
    # Only years that actually occur in the data. Gap years
    # with no events and no disposals get no report.
    years = {tax_year_of(event.date) for event in timeline}
    years.update(tax_year_of(d.sell_date) for d in disposals)
    return sorted(years, key=lambda y: y.start_year)


def is_current(tax_year: TaxYear, as_of: date) -> bool:
    # as_of is the report production date, not the last
    # event date. Final only once as_of is past year end
    # + 30 days: after that, no later buy can still match
    # a sale in this year, and the year itself has ended.
    return not (as_of > tax_year.end + timedelta(days=BED_AND_BREAKFAST_DAYS))


def disposals_for_year(disposals: list[Disposal], tax_year: TaxYear) -> list[Disposal]:
    return [d for d in disposals if tax_year_of(d.sell_date) == tax_year]


def events_for_year(
    timeline: list[Event], year_disposals: list[Disposal], tax_year: TaxYear
) -> tuple[list[Event], bool]:
    # All events up to year end, accumulating from the start,
    # plus any buy after year end that matches a disposal in
    # this year. Those buys are shown for audit only: they
    # justify the matching, they are not counted in holdings.
    # Timeline order is already chronological, filtering
    # preserves it, so no re-sort is needed.
    future_buys = {
        (d.buy_date, d.symbol)
        for d in year_disposals
        if d.match_type != MatchType.POOL
        and d.buy_date is not None
        and d.buy_date > tax_year.end
    }
    events = [
        event
        for event in timeline
        if event.date <= tax_year.end
        or (event.action == Action.BUY and (event.date, event.symbol) in future_buys)
    ]
    has_future_buys = any(event.date > tax_year.end for event in events)
    return events, has_future_buys


def holdings_at_year_end(
    timeline: list[Event],
    disposals: list[Disposal],
    tax_year_end: date,
) -> dict[str, tuple[int, Decimal]]:
    # Pool at year end, derived from the finished run, not
    # by snapshotting mid-run. Per (ticker, buy date):
    # buy cost minus the cost of the part of that buy that
    # was consumed matching a sale, is what entered the pool
    # on that date. Pool disposals up to year end then
    # subtract their units and cost. A sale matched by a
    # later buy never touches the pool, so it does not
    # subtract here; a pool disposal does, even if it was
    # only resolved after year end, because its sell date
    # is in this year.
    buy_totals: dict[tuple[str, date], list] = {}
    for event in timeline:
        if event.action != Action.BUY or event.date > tax_year_end:
            continue
        total = buy_totals.setdefault(event.symbol, {}).setdefault(
            event.date, [0, Decimal(0)]
        )
        total[0] += event.quantity
        total[1] += event.pricing.amount + event.pricing.fees
    matched: dict[tuple[str, date], list] = {}
    for disposal in disposals:
        if (
            disposal.match_type not in (MatchType.SAME_DAY, MatchType.BED_AND_BREAKFAST)
            or disposal.buy_date is None
        ):
            continue
        total = matched.setdefault(disposal.symbol, {}).setdefault(
            disposal.buy_date, [0, Decimal(0)]
        )
        total[0] += disposal.units
        total[1] += disposal.cost
    holdings: dict[str, list] = {}
    for symbol, by_date in buy_totals.items():
        for buy_date, (quantity, cost) in by_date.items():
            matched_quantity, matched_cost = matched.get(symbol, {}).get(
                buy_date, [0, Decimal(0)]
            )
            pool = holdings.setdefault(symbol, [0, Decimal(0)])
            pool[0] += quantity - matched_quantity
            pool[1] += cost - matched_cost
    for disposal in disposals:
        if disposal.match_type != MatchType.POOL:
            continue
        if disposal.sell_date > tax_year_end:
            continue
        pool = holdings.setdefault(disposal.symbol, [0, Decimal(0)])
        pool[0] -= disposal.units
        pool[1] -= disposal.cost
    return {symbol: (units, cost) for symbol, (units, cost) in sorted(holdings.items())}
