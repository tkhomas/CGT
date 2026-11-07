"""Tests for buy/sell matching: matching.py, via dispatch.

Events are built in GBP directly (rate 1), so FX does not
muddy the matcher. The timeline is built with
build_global_timeline, then run through dispatch, exactly
as main does. Tests check disposals (match type, units,
proceeds, cost, gain) and the pool left afterwards -- the
real logic, not the queue or other internals.

Two tests are marked expectedFailure. They encode the two
known bugs, in the correct HMRC behaviour, and they fail
on the current queue matcher:
  1. a same-day buy must match a same-day sell before an
     older bed-and-breakfast sell, and
  2. several buys on the same day are one lot at their
     average cost, not FIFO lots.
When the two-pass matcher lands, those two should pass,
and the decorators come off.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from matching import Disposal, MatchType, PoolInsufficientError, State
from models import Action, Currency, Event, Pricing
from portfolio import Portfolio
from processors import dispatch
from timeline import build_global_timeline


def _event(
    symbol: str,
    when: date,
    action: Action,
    quantity: int,
    price: int | Decimal,
    fees: int | Decimal = Decimal(0),
    provider: str = "Test Broker",
) -> Event:
    amount = Decimal(quantity) * Decimal(price)
    return Event(
        date=when,
        action=action,
        symbol=symbol,
        description="",
        quantity=quantity,
        pricing=Pricing(
            original_currency=Currency.GBP,
            original_price=Decimal(price),
            original_amount=amount,
            original_fees=Decimal(fees),
            original_units_per_gbp=Decimal(1),
            fx_source="GBP",
        ),
        provider=provider,
        source_file="test.csv",
        row=1,
    )


def _run(events: list[Event]) -> tuple[list[Disposal], Portfolio]:
    """Run a list of events through the timeline and dispatch.

    Returns (disposals, portfolio). Disposals are in the
    order the matcher created them.
    """
    state = State()
    for event in build_global_timeline(events):
        dispatch(event, state)
    state.matcher.finish(state.portfolio)
    return state.matcher.gains.disposals, state.portfolio


def _pool(portfolio: Portfolio, symbol: str) -> tuple[int, Decimal]:
    pool = portfolio.get_pool(symbol)
    return pool.units, pool.total_cost


class PoolTests(unittest.TestCase):
    def test_pool_sell(self) -> None:
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10),
                _event("ACME", date(2025, 3, 1), Action.SELL, 5, 20),
            ]
        )
        self.assertEqual(len(disposals), 1)
        disposal = disposals[0]
        self.assertEqual(disposal.match_type, MatchType.POOL)
        self.assertEqual(disposal.units, 5)
        self.assertEqual(disposal.proceeds, Decimal(100))
        self.assertEqual(disposal.cost, Decimal(50))
        self.assertEqual(disposal.gain, Decimal(50))
        self.assertEqual(_pool(portfolio, "ACME"), (5, Decimal(50)))

    def test_pool_average_is_recalculated_on_each_buy(self) -> None:
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10),
                _event("ACME", date(2025, 1, 2), Action.BUY, 10, 20),
                _event("ACME", date(2025, 3, 1), Action.SELL, 10, 30),
            ]
        )
        # Pool average is £15, so 10 units cost £150.
        self.assertEqual(disposals[0].cost, Decimal(150))
        self.assertEqual(disposals[0].gain, Decimal(150))
        self.assertEqual(_pool(portfolio, "ACME"), (10, Decimal(150)))

    def test_fees_are_in_cost_and_proceeds(self) -> None:
        disposals, _portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10, fees=5),
                _event("ACME", date(2025, 3, 1), Action.SELL, 10, 20, fees=5),
            ]
        )
        # Buy cost is amount + fees (£105), sell proceeds
        # are amount - fees (£195).
        self.assertEqual(disposals[0].cost, Decimal(105))
        self.assertEqual(disposals[0].proceeds, Decimal(195))
        self.assertEqual(disposals[0].gain, Decimal(90))

    def test_pool_insufficient_screams(self) -> None:
        state = State()
        events = build_global_timeline(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 5, 10),
                _event("ACME", date(2025, 3, 1), Action.SELL, 10, 20),
            ]
        )
        with self.assertRaises(PoolInsufficientError):
            for event in events:
                dispatch(event, state)
            state.matcher.finish(state.portfolio)
        self.assertEqual(state.matcher.gains.disposals, [])


class SameDayAndBedAndBreakfastTests(unittest.TestCase):
    def test_same_day_sell(self) -> None:
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 4, 16), Action.BUY, 10, 10),
                _event("ACME", date(2025, 4, 16), Action.SELL, 5, 20),
            ]
        )
        self.assertEqual(len(disposals), 1)
        self.assertEqual(disposals[0].match_type, MatchType.SAME_DAY)
        self.assertEqual(disposals[0].cost, Decimal(50))
        self.assertEqual(disposals[0].gain, Decimal(50))
        # The surplus same-day buy goes to the pool.
        self.assertEqual(_pool(portfolio, "ACME"), (5, Decimal(50)))

    def test_same_day_sell_with_preexisting_pool(self) -> None:
        # The current same-day test has nothing else to
        # match with, so it cannot prove the pool was left
        # alone. Here there is a pool first: 5 units held
        # for a while at £10. A 5-unit buy and a 5-unit
        # sell on the same day must match each other, and
        # the pool must come out exactly as it went in.
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 5, 10),
                _event("ACME", date(2025, 4, 16), Action.BUY, 5, 20),
                _event("ACME", date(2025, 4, 16), Action.SELL, 5, 30),
            ]
        )
        self.assertEqual(len(disposals), 1)
        self.assertEqual(disposals[0].match_type, MatchType.SAME_DAY)
        self.assertEqual(disposals[0].cost, Decimal(100))
        self.assertEqual(disposals[0].proceeds, Decimal(150))
        self.assertEqual(disposals[0].gain, Decimal(50))
        self.assertEqual(_pool(portfolio, "ACME"), (5, Decimal(50)))

    def test_same_day_sell_split_between_same_day_buy_and_pool(self) -> None:
        # Same-day first, pool second, with a preexisting
        # pool at a different price so the two cannot be
        # confused: a sell matched to the pool would cost
        # £10 a unit, matched same-day it costs £20.
        # Pool 20 at £10, sell 15 at £30 on 16 Apr, buy
        # 10 at £20 the same day: 10 units same-day, the
        # other 5 from the pool, and the pool loses only
        # those 5.
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 20, 10),
                _event("ACME", date(2025, 4, 16), Action.BUY, 10, 20),
                _event("ACME", date(2025, 4, 16), Action.SELL, 15, 30),
            ]
        )
        by_type = {d.match_type: d for d in disposals}
        self.assertEqual(len(disposals), 2)
        self.assertEqual(by_type[MatchType.SAME_DAY].units, 10)
        self.assertEqual(by_type[MatchType.SAME_DAY].cost, Decimal(200))
        self.assertEqual(by_type[MatchType.POOL].units, 5)
        self.assertEqual(by_type[MatchType.POOL].cost, Decimal(50))
        self.assertEqual(_pool(portfolio, "ACME"), (15, Decimal(150)))

    def test_bed_and_breakfast_within_30_days(self) -> None:
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10),
                _event("ACME", date(2025, 1, 10), Action.SELL, 10, 20),
                _event("ACME", date(2025, 1, 20), Action.BUY, 10, 15),
            ]
        )
        self.assertEqual(len(disposals), 1)
        self.assertEqual(disposals[0].match_type, MatchType.BED_AND_BREAKFAST)
        self.assertEqual(disposals[0].buy_date, date(2025, 1, 20))
        self.assertEqual(disposals[0].cost, Decimal(150))
        self.assertEqual(disposals[0].gain, Decimal(50))
        # A bed-and-breakfast sell never touches the pool.
        self.assertEqual(_pool(portfolio, "ACME"), (10, Decimal(100)))

    def test_bed_and_breakfast_sell_split_between_buy_and_pool(self) -> None:
        # Bed-and-breakfast first, pool second, with a
        # preexisting pool at a different price so the two
        # cannot be confused: matched to the pool a unit
        # costs £10, matched bed-and-breakfast it costs £15.
        # Pool 20 at £10, sell 15 at £20 on 10 Jan, buy
        # 10 at £15 on 20 Jan: 10 units bed-and-breakfast,
        # the other 5 from the pool, and the pool loses
        # only those 5.
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 1, 1), Action.BUY, 20, 10),
                _event("ACME", date(2025, 1, 10), Action.SELL, 15, 20),
                _event("ACME", date(2025, 1, 20), Action.BUY, 10, 15),
            ]
        )
        by_type = {d.match_type: d for d in disposals}
        self.assertEqual(len(disposals), 2)
        self.assertEqual(by_type[MatchType.BED_AND_BREAKFAST].units, 10)
        self.assertEqual(by_type[MatchType.BED_AND_BREAKFAST].cost, Decimal(150))
        self.assertEqual(by_type[MatchType.POOL].units, 5)
        self.assertEqual(by_type[MatchType.POOL].cost, Decimal(50))
        self.assertEqual(_pool(portfolio, "ACME"), (15, Decimal(150)))

    def test_bed_and_breakfast_boundary_30_and_31_days(self) -> None:
        # A buy exactly 30 days after a sell still matches;
        # a buy 31 days after does not, and the sell goes
        # to the pool instead.
        for label, buy_date, expected_type, expected_pool in [
            (
                "30 days",
                date(2025, 2, 9),
                MatchType.BED_AND_BREAKFAST,
                (10, Decimal(100)),
            ),
            ("31 days", date(2025, 2, 10), MatchType.POOL, (10, Decimal(150))),
        ]:
            with self.subTest(label=label):
                disposals, portfolio = _run(
                    [
                        _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10),
                        _event("ACME", date(2025, 1, 10), Action.SELL, 10, 20),
                        _event("ACME", buy_date, Action.BUY, 10, 15),
                    ]
                )
                self.assertEqual(len(disposals), 1)
                self.assertEqual(disposals[0].match_type, expected_type)
                self.assertEqual(_pool(portfolio, "ACME"), expected_pool)

    def test_split_matches(self) -> None:
        # One sell split across two buys, and one buy split
        # across two sells, are the same outcome: each
        # matched portion is its own disposal, with its own
        # buy date and cost.
        with self.subTest("one sell, two buys"):
            disposals, _ = _run(
                [
                    _event("ACME", date(2025, 1, 1), Action.BUY, 10, 10),
                    _event("ACME", date(2025, 1, 10), Action.SELL, 10, 20),
                    _event("ACME", date(2025, 1, 20), Action.BUY, 6, 15),
                    _event("ACME", date(2025, 1, 25), Action.BUY, 4, 18),
                ]
            )
            self.assertEqual(
                [(d.units, d.cost, d.buy_date) for d in disposals],
                [
                    (6, Decimal(90), date(2025, 1, 20)),
                    (4, Decimal(72), date(2025, 1, 25)),
                ],
            )
        with self.subTest("two sells, one buy"):
            disposals, _ = _run(
                [
                    _event("ACME", date(2025, 1, 1), Action.BUY, 20, 10),
                    _event("ACME", date(2025, 1, 10), Action.SELL, 6, 20),
                    _event("ACME", date(2025, 1, 12), Action.SELL, 4, 20),
                    _event("ACME", date(2025, 1, 20), Action.BUY, 10, 15),
                ]
            )
            self.assertEqual(
                [(d.units, d.cost, d.sell_date) for d in disposals],
                [
                    (6, Decimal(90), date(2025, 1, 10)),
                    (4, Decimal(60), date(2025, 1, 12)),
                ],
            )

    def test_cross_provider_same_ticker_shares_one_pool(self) -> None:
        # Provider is audit only: the same ticker from two
        # brokers is one holding, one pool.
        disposals, portfolio = _run(
            [
                _event(
                    "ACME", date(2025, 1, 1), Action.BUY, 10, 10, provider="Broker A"
                ),
                _event(
                    "ACME", date(2025, 3, 1), Action.SELL, 5, 20, provider="Broker B"
                ),
            ]
        )
        self.assertEqual(disposals[0].match_type, MatchType.POOL)
        self.assertEqual(disposals[0].gain, Decimal(50))
        self.assertEqual(_pool(portfolio, "ACME"), (5, Decimal(50)))

    def test_same_day_order_does_not_depend_on_input_order(self) -> None:
        # The buy is listed first in the input, but the
        # timeline sorts sells before buys on the same day,
        # so the match is still same-day.
        disposals, _ = _run(
            [
                _event("ACME", date(2025, 4, 16), Action.BUY, 5, 10),
                _event("ACME", date(2025, 4, 16), Action.SELL, 5, 20),
            ]
        )
        self.assertEqual(disposals[0].match_type, MatchType.SAME_DAY)


class KnownBugsTests(unittest.TestCase):
    @unittest.expectedFailure
    def test_same_day_takes_priority_over_bed_and_breakfast(self) -> None:
        # HMRC order is same-day first, then 30 days, then
        # pool. A buy on 20 Apr must match the 20 Apr sell
        # before the 10 Apr sell, which then goes to the pool.
        disposals, _ = _run(
            [
                _event("ACME", date(2025, 4, 1), Action.BUY, 10, 10),
                _event("ACME", date(2025, 4, 10), Action.SELL, 5, 20),
                _event("ACME", date(2025, 4, 20), Action.SELL, 5, 20),
                _event("ACME", date(2025, 4, 20), Action.BUY, 5, 15),
            ]
        )
        by_sell_date = {d.sell_date: d for d in disposals}
        self.assertEqual(by_sell_date[date(2025, 4, 20)].match_type, MatchType.SAME_DAY)
        self.assertEqual(by_sell_date[date(2025, 4, 10)].match_type, MatchType.POOL)

    @unittest.expectedFailure
    def test_same_day_buys_are_averaged(self) -> None:
        # All buys on the same day are one lot at their
        # average cost: 5 at £10 and 5 at £20 is £15 a unit.
        # A 5-unit same-day sell therefore costs £75, and
        # the 5-unit remainder in the pool costs £75 too.
        disposals, portfolio = _run(
            [
                _event("ACME", date(2025, 4, 16), Action.BUY, 5, 10),
                _event("ACME", date(2025, 4, 16), Action.BUY, 5, 20),
                _event("ACME", date(2025, 4, 16), Action.SELL, 5, 30),
            ]
        )
        self.assertEqual(len(disposals), 1)
        self.assertEqual(disposals[0].match_type, MatchType.SAME_DAY)
        self.assertEqual(disposals[0].cost, Decimal(75))
        self.assertEqual(disposals[0].gain, Decimal(75))
        self.assertEqual(_pool(portfolio, "ACME"), (5, Decimal(75)))


if __name__ == "__main__":
    unittest.main()
