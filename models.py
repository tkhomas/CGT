"""Shared types: enums and internal structures."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path


class Action(Enum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"


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
    name: str  # folder name under input/, not a field in config.json
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
