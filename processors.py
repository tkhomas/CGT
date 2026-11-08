"""Processor registry and dispatch, via decorator.

Adding a new event type means writing a processor function
and decorating it with @register(Action.X). No other file
changes, no registry dict to edit by hand.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from matching import State
from models import Action, Event

ProcessorFunc = Callable[[Event, State], None]

PROCESSORS: dict[Action, ProcessorFunc] = {}


def register(*actions: Action) -> Callable[[ProcessorFunc], ProcessorFunc]:
    def decorator(func: ProcessorFunc) -> ProcessorFunc:
        for action in actions:
            PROCESSORS[action] = func
        return func

    return decorator


@register(Action.BUY, Action.SELL)
def process_buy_sell(event: Event, state: State) -> None:
    state.matcher.process(event, state.portfolio)


def dispatch(event: Event, state: State) -> None:
    processor = PROCESSORS.get(event.action)
    if processor is None:
        print(
            f"Warning: no processor for Action {event.action.value!r} "
            f"for {event.symbol} on {event.date}, event dropped",
            file=sys.stderr,
        )
        return
    processor(event, state)
