"""Buy/Sell matching: same-day, 30-day, then Section 104 pool.

Two passes over the whole timeline, which is already loaded
in memory. The pool is never changed while matching is
still being decided.

Pass 1, matching, buy-centric and with no pool changes:
  * Buys for one ticker on one day are one lot, at their
    average cost. All shares of the same class acquired
    on the same day are treated as one transaction
    (TCGA92/S105(1)), so there is no order between
    buys on the same day in a date-only CSV.
  * Buy lots are walked in chronological order. Each lot
    is allocated first to same-day sells for that ticker,
    then to still-unmatched sells from the previous 30
    days, oldest first. That is the HMRC order: same-day
    first, bed-and-breakfast second, pool third.
  * Each matched portion becomes a Disposal, with its
    own cost, proceeds, match type, and buy date.
    A sell left partly unmatched after both steps is a
    pool disposal, costed in pass 2.

Pass 2, pool, chronological, no lookahead:
  * The timeline is walked in date order, sells before
    buys on the same day. A buy lot adds only its
    remainder -- total lot minus what pass 1 allocated
    -- to the pool. A pool disposal is costed at the
    pool average at the sell date, before any later
    buy has entered the pool, and the pool is reduced
    by that disposal there and then.
  * The pool after each pool-changing event is recorded
    in pool_snapshots, so holdings can be read from here.

Gains is a collection of Disposals. A Disposal is one gain,
a matched portion of a Sell, with its own cost, proceeds,
match type, and buy date if any. One Sell can become
several Disposals. Disposals are sorted at the end of
finish() by sell date, then match order (same-day,
bed-and-breakfast, pool), then buy date, which is the
order reporting presents them in.

UK CGT only, so all values must be in GBP. Conversion
to GBP is via fx.convert_events before matching.

Pool insufficient should never happen. When it does, it
screams with a PoolInsufficientError, and main prints
all events for that ticker. No cost-0 disposal is ever
created for it.
"""

from __future__ import annotations

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
class PoolSnapshot:
    date: date
    symbol: str
    units: int
    total_cost: Decimal


@dataclass
class _BuyLot:
    symbol: str
    date: date
    units: int
    total_cost: Decimal
    remaining_units: int = 0
    remaining_cost: Decimal = Decimal(0)

    @property
    def cost_per_unit(self) -> Decimal:
        return self.total_cost / self.units


@dataclass
class Matcher:
    # Events collected by process(), matched and pooled by
    # finish(). process() itself changes nothing: matching
    # needs the whole timeline, including buys that arrive
    # after a sell, so no disposal can be decided on arrival.
    # Events must arrive in timeline order, as built by
    # build_global_timeline; both passes rely on that order
    # and do not sort again.
    events: list[Event] = field(default_factory=list)
    gains: Gains = field(default_factory=Gains)
    pool_snapshots: list[PoolSnapshot] = field(default_factory=list)

    def process(self, event: Event, portfolio: Portfolio) -> None:
        self.events.append(event)

    def finish(self, portfolio: Portfolio) -> None:
        sells, buy_lots = self._matching_pass()
        self._pool_pass(sells, buy_lots, portfolio)
        match_order = {
            MatchType.SAME_DAY: 0,
            MatchType.BED_AND_BREAKFAST: 1,
            MatchType.POOL: 2,
        }
        self.gains.disposals.sort(
            key=lambda d: (
                d.sell_date,
                d.symbol,
                match_order[d.match_type],
                d.buy_date or date.min,
            )
        )

    def _matching_pass(self) -> tuple[list[PendingSell], list[_BuyLot]]:
        # Lots and sells are both in timeline order. Each
        # lot is walked once, oldest first, and each sell
        # is taken one by one, in order, never revisited
        # once it is filled or out of the 30-day window.
        lots, sells = self._group_events()
        sells_by_symbol: dict[str, list[PendingSell]] = {}
        for sell in sells:
            sells_by_symbol.setdefault(sell.event.symbol, []).append(sell)
        # Index of the first unmatched sell per ticker.
        # Sells before it are filled, or more than 30 days
        # old and therefore pool-only; neither can match
        # a later lot again.
        first_unmatched: dict[str, int] = {}
        for lot in lots:
            lot.remaining_units = lot.units
            lot.remaining_cost = lot.total_cost
            symbol_sells = sells_by_symbol.get(lot.symbol, [])
            first_unmatched[lot.symbol] = self._match_lot(
                lot, symbol_sells, first_unmatched.get(lot.symbol, 0)
            )
        return sells, lots

    def _group_events(self) -> tuple[list[_BuyLot], list[PendingSell]]:
        # Buys grouped by (ticker, date) into one lot each,
        # at their average cost; sells kept in order.
        buy_lots: dict[tuple[str, date], _BuyLot] = {}
        sells: list[PendingSell] = []
        for event in self.events:
            if event.action == Action.BUY:
                key = (event.symbol, event.date)
                cost = event.pricing.amount + event.pricing.fees
                lot = buy_lots.get(key)
                if lot is None:
                    buy_lots[key] = _BuyLot(
                        symbol=event.symbol,
                        date=event.date,
                        units=event.quantity,
                        total_cost=cost,
                    )
                else:
                    lot.units += event.quantity
                    lot.total_cost += cost
            elif event.action == Action.SELL:
                proceeds_per_unit = (
                    event.pricing.amount - event.pricing.fees
                ) / event.quantity
                sells.append(
                    PendingSell(
                        event=event,
                        remaining_units=event.quantity,
                        proceeds_per_unit=proceeds_per_unit,
                    )
                )
        return list(buy_lots.values()), sells

    def _match_lot(
        self, lot: _BuyLot, symbol_sells: list[PendingSell], index: int
    ) -> int:
        while lot.remaining_units > 0:
            index = self._advance_to_live_sell(symbol_sells, index, lot.date)
            # No unmatched sell on or before this lot's date:
            # every later sell is in the future, so the rest
            # of the lot goes to the pool.
            if index >= len(symbol_sells) or symbol_sells[index].event.date > lot.date:
                break
            oldest = symbol_sells[index]
            if oldest.event.date == lot.date:
                self._allocate(lot, oldest, MatchType.SAME_DAY)
                if oldest.remaining_units == 0:
                    index += 1
                continue
            # Oldest unmatched sell is a bed-and-breakfast
            # sell. Same-day sells, if any, match first.
            same_day = self._find_same_day(symbol_sells, index, lot.date)
            if same_day is not None:
                self._match_same_day(lot, symbol_sells, same_day)
                if lot.remaining_units == 0:
                    break
            self._allocate(lot, oldest, MatchType.BED_AND_BREAKFAST)
            if oldest.remaining_units == 0:
                index += 1
        return index

    def _advance_to_live_sell(
        self, symbol_sells: list[PendingSell], index: int, lot_date: date
    ) -> int:
        # Same-day sells are allocated out of order, so the
        # first unmatched sell may be a filled same-day sell
        # by now. Skip filled sells before looking at it.
        while index < len(symbol_sells) and symbol_sells[index].remaining_units == 0:
            index += 1
        # A sell more than 30 days before this lot can never
        # match a buy again. Later lots are later still, so
        # it is pool-only now.
        while (
            index < len(symbol_sells)
            and (lot_date - symbol_sells[index].event.date).days
            > BED_AND_BREAKFAST_DAYS
        ):
            index += 1
        return index

    def _find_same_day(
        self, symbol_sells: list[PendingSell], index: int, lot_date: date
    ) -> int | None:
        # First sell on the lot's date, if any, scanning
        # forward from the oldest unmatched sell.
        same_day = index
        while (
            same_day < len(symbol_sells)
            and symbol_sells[same_day].event.date < lot_date
        ):
            same_day += 1
        if (
            same_day < len(symbol_sells)
            and symbol_sells[same_day].event.date == lot_date
        ):
            return same_day
        return None

    def _match_same_day(
        self, lot: _BuyLot, symbol_sells: list[PendingSell], same_day: int
    ) -> None:
        while (
            lot.remaining_units > 0
            and same_day < len(symbol_sells)
            and symbol_sells[same_day].event.date == lot.date
        ):
            sell = symbol_sells[same_day]
            if sell.remaining_units == 0:
                # Already filled on an earlier pass through
                # this lot; skip it.
                same_day += 1
                continue
            self._allocate(lot, sell, MatchType.SAME_DAY)
            if sell.remaining_units == 0:
                same_day += 1

    def _allocate(self, lot: _BuyLot, sell: PendingSell, match_type: MatchType) -> None:
        matched = min(lot.remaining_units, sell.remaining_units)
        cost = matched * lot.cost_per_unit
        self.gains.disposals.append(
            Disposal(
                date=sell.event.date,
                symbol=sell.event.symbol,
                units=matched,
                proceeds=matched * sell.proceeds_per_unit,
                cost=cost,
                match_type=match_type,
                sell_date=sell.event.date,
                buy_date=lot.date,
            )
        )
        sell.remaining_units -= matched
        lot.remaining_units -= matched
        lot.remaining_cost -= cost

    def _pool_pass(
        self, sells: list[PendingSell], buy_lots: list[_BuyLot], portfolio: Portfolio
    ) -> None:
        # Pool events in timeline order: sells before buy
        # lots on the same day, as in build_global_timeline.
        # A buy lot's remainder enters the pool on its date;
        # a pool disposal is costed at the pool average on
        # its sell date, before later buys enter the pool.
        pool_sells = [s for s in sells if s.remaining_units > 0]
        events: list[tuple[date, int, object]] = []
        for sell in pool_sells:
            events.append((sell.event.date, 0, sell))
        for lot in buy_lots:
            if lot.remaining_units > 0:
                events.append((lot.date, 1, lot))
        events.sort(key=lambda item: (item[0], item[1]))
        for _, _, item in events:
            if isinstance(item, PendingSell):
                self._match_to_pool(item, portfolio)
            else:
                lot = item
                pool = portfolio.get_pool(lot.symbol)
                pool.units += lot.remaining_units
                pool.total_cost += lot.remaining_cost
                self.pool_snapshots.append(
                    PoolSnapshot(
                        date=lot.date,
                        symbol=lot.symbol,
                        units=pool.units,
                        total_cost=pool.total_cost,
                    )
                )

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
        self.pool_snapshots.append(
            PoolSnapshot(
                date=pending.event.date,
                symbol=pending.event.symbol,
                units=pool.units,
                total_cost=pool.total_cost,
            )
        )


@dataclass
class State:
    portfolio: Portfolio = field(default_factory=Portfolio)
    matcher: Matcher = field(default_factory=Matcher)
    # Income events, collected in timeline order. The sums
    # per tax year are made at reporting, not here.
    income: list[Event] = field(default_factory=list)
