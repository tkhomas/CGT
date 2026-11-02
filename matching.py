"""Buy/Sell matching: same-day, 30-day, then Section 104 pool.

Sells are queued, not resolved straight away. A Buy first matches
queued Sells for the same ticker, same-day first, then within 30 days.
Anything left from a Buy goes to the pool. Sells older than 30 days,
and all remaining Sells at the end, resolve against the pool.

Gains is a collection of Disposals. A Disposal is one gain, a matched
portion of a Sell, with its own cost, proceeds, match type, and buy
date if any. One Sell can become several Disposals.

Gains are global for now, no tax-year split yet.

UK CGT only, so all values must be in GBP. Conversion to GBP
is a separate step before matching, not yet implemented, so
for now amounts are used as given.

Pool insufficient should never happen. When it does, it screams
with a PoolInsufficientError, and main prints all events for that
ticker. No cost-0 disposal is ever created for it.
"""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from models import Action, Event
from portfolio import Portfolio

BED_AND_BREAKFAST_DAYS = 30


class PoolInsufficientError(RuntimeError):
    def __init__(self, symbol: str, message: str) -> None:
        super().__init__(message)
        self.symbol = symbol


class MatchType(Enum):
    SAME_DAY = "same-day"
    BED_AND_BREAKFAST = "bed-and-breakfast"
    POOL = "pool"


@dataclass
class PendingSell:
    event: Event
    remaining_units: int
    proceeds_per_unit: Decimal


@dataclass
class Disposal:
    date: date
    symbol: str
    units: int
    proceeds: Decimal
    cost: Decimal
    match_type: MatchType
    sell_date: date
    buy_date: date | None = None

    @property
    def gain(self) -> Decimal:
        return self.proceeds - self.cost


@dataclass
class Gains:
    disposals: list[Disposal] = field(default_factory=list)

    @property
    def total_proceeds(self) -> Decimal:
        return sum((d.proceeds for d in self.disposals), Decimal(0))

    @property
    def total_cost(self) -> Decimal:
        return sum((d.cost for d in self.disposals), Decimal(0))

    @property
    def total_gain(self) -> Decimal:
        return sum((d.gain for d in self.disposals), Decimal(0))


@dataclass
class Matcher:
    queues: dict[str, deque[PendingSell]] = field(default_factory=dict)
    gains: Gains = field(default_factory=Gains)

    def process(self, event: Event, portfolio: Portfolio) -> None:
        self._resolve_queue(portfolio, event.date)
        if event.action == Action.SELL:
            self._process_sell(event)
        elif event.action == Action.BUY:
            self._process_buy(event, portfolio)

    def _resolve_queue(self, portfolio: Portfolio, as_of: date | None = None) -> None:
        """
        This only works if timeline is sorted by date
        as_of == None means force the match
        """
        for queue in self.queues.values():
            while queue and (
                as_of is None
                or (as_of - queue[0].event.date).days > BED_AND_BREAKFAST_DAYS
            ):
                self._match_to_pool(queue.popleft(), portfolio)

    def _process_sell(self, event: Event) -> None:
        proceeds_per_unit = (event.amount - event.fees) / event.quantity
        self.queues.setdefault(event.symbol, deque()).append(
            PendingSell(
                event=event,
                remaining_units=event.quantity,
                proceeds_per_unit=proceeds_per_unit,
            )
        )

    def _process_buy(self, event: Event, portfolio: Portfolio) -> None:
        buy_remaining_units = event.quantity
        cost_per_unit = (event.amount + event.fees) / event.quantity
        queue = self.queues.setdefault(event.symbol, deque())
        while buy_remaining_units > 0 and queue:
            pending_sell = queue[0]
            matched = min(buy_remaining_units, pending_sell.remaining_units)
            match_type = (
                MatchType.SAME_DAY
                if event.date == pending_sell.event.date
                else MatchType.BED_AND_BREAKFAST
            )
            self.gains.disposals.append(
                Disposal(
                    date=pending_sell.event.date,
                    symbol=event.symbol,
                    units=matched,
                    proceeds=matched * pending_sell.proceeds_per_unit,
                    cost=matched * cost_per_unit,
                    match_type=match_type,
                    sell_date=pending_sell.event.date,
                    buy_date=event.date,
                )
            )
            pending_sell.remaining_units -= matched
            buy_remaining_units -= matched
            if pending_sell.remaining_units == 0:
                queue.popleft()

        # We still have something left to add to the pool after resolving all sells
        if buy_remaining_units > 0:
            pool = portfolio.get_pool(event.symbol)
            pool.units += buy_remaining_units
            pool.total_cost += buy_remaining_units * cost_per_unit

    def _match_to_pool(self, pending: PendingSell, portfolio: Portfolio) -> None:
        pool = portfolio.get_pool(pending.event.symbol)
        sell_units = pending.remaining_units

        if pool.units < sell_units:
            raise PoolInsufficientError(
                pending.event.symbol,
                f"Pool insufficient for {pending.event.symbol} "
                f"on {pending.event.date}: sold {sell_units} units, "
                f"pool holds {pool.units} units. "
                f"Sell from {pending.event.source_file}:"
                f"{pending.event.row}. This should never happen, "
                "the data is incomplete.",
            )
        avg_cost = pool.total_cost / pool.units
        self.gains.disposals.append(
            Disposal(
                date=pending.event.date,
                symbol=pending.event.symbol,
                units=sell_units,
                proceeds=sell_units * pending.proceeds_per_unit,
                cost=sell_units * avg_cost,
                match_type=MatchType.POOL,
                sell_date=pending.event.date,
            )
        )
        pool.units -= sell_units
        pool.total_cost -= sell_units * avg_cost
        pending.remaining_units = 0

    def finish(self, portfolio: Portfolio) -> None:
        self._resolve_queue(portfolio)


@dataclass
class State:
    portfolio: Portfolio = field(default_factory=Portfolio)
    matcher: Matcher = field(default_factory=Matcher)
