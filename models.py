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


# What parsing knows, before FX. No Pricing yet, because
# Pricing requires a rate, and parsing does not know it.
@dataclass
class ParsedEvent:
    date: date
    action: Action
    symbol: str
    description: str
    quantity: int
    original_currency: Currency
    original_price: Decimal
    original_amount: Decimal
    original_fees: Decimal
    provider: str
    source_file: str
    row: int


@dataclass
class Pricing:
    original_currency: Currency
    original_price: Decimal
    original_amount: Decimal
    original_fees: Decimal
    original_units_per_gbp: Decimal
    fx_source: str

    @property
    def price(self) -> Decimal:
        return self.original_price / self.original_units_per_gbp

    @property
    def amount(self) -> Decimal:
        return self.original_amount / self.original_units_per_gbp

    @property
    def fees(self) -> Decimal:
        return self.original_fees / self.original_units_per_gbp

    @property
    def currency(self) -> Currency:
        return Currency.GBP


@dataclass
class Event:
    date: date
    action: Action
    symbol: str
    description: str
    quantity: int
    pricing: Pricing
    provider: str
    source_file: str
    row: int
