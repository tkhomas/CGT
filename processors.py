"""Processor registry and dispatch, via decorator.

Adding a new event type means writing a processor function
and decorating it with @register(Action.X). No other file
changes, no registry dict to edit by hand.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from models import Action, Event
from portfolio import Portfolio

ProcessorFunc = Callable[[Event, Portfolio], None]

PROCESSORS: dict[Action, ProcessorFunc] = {}


def register(*actions: Action) -> Callable[[ProcessorFunc], ProcessorFunc]:
    def decorator(func: ProcessorFunc) -> ProcessorFunc:
        for action in actions:
            PROCESSORS[action] = func
        return func

    return decorator


@register(Action.BUY, Action.SELL)
def do_nothing(event: Event, portfolio: Portfolio) -> None:
    """Intentionally do nothing, for now.

    Buy and Sell currently have no matching logic yet, so
    they do nothing to the portfolio. When the matcher is
    built, it will replace this registration for Buy and
    Sell in the same chunk. do_nothing itself stays, for
    events later that truly should be ignored.
    """
    return None


def dispatch(event: Event, portfolio: Portfolio) -> None:
    processor = PROCESSORS.get(event.action)
    if processor is None:
        print(
            f"Warning: no processor for Action {event.action.value!r} "
            f"for {event.symbol} on {event.date}, event dropped",
            file=sys.stderr,
        )
        return
    processor(event, portfolio)
