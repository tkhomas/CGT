"""Shared types: enums and internal structures."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path


class Action(Enum):
    BUY = "buy"
    SELL = "sell"


# Global ordering within a single date, as a simple list.
# Earlier in the list means earlier in processing that day.
# Dividend before Interest here is arbitrary, their relative
# order does not matter. A new action is added to this list
# in the same chunk as its processor, never before.
ACTION_ORDER: list[Action] = [
    Action.SELL,
    Action.BUY,
]


class Currency(Enum):
    GBP = "GBP"
    USD = "USD"
    EUR = "EUR"
    CHF = "CHF"
    JPY = "JPY"
    CAD = "CAD"
    AUD = "AUD"


@dataclass
class ProviderConfig:
    name: str
    date_format: str
    currency: Currency


@dataclass
class ProviderInput:
    path: Path
    provider: ProviderConfig


@dataclass
class Event:
    date: date
    action: Action
    symbol: str
    description: str
    quantity: int
    price: Decimal
    fees: Decimal
    amount: Decimal
    currency: Currency
    provider: str
    source_file: str
    row: int
