"""Shared types: enums and internal structures."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from pathlib import Path


class Action(Enum):
    BUY = "buy"
    SELL = "sell"
    STOCK_PLAN_ACTIVITY = "stock plan activity"
    WIRE_SENT = "wire sent"
    DIVIDEND = "dividend"
    INTEREST = "interest"
    # Tax already withheld from income: the only tax
    # action. A tax row does not say whether it is tax on
    # a dividend or on interest, and it is never rewritten:
    # reporting decides that from the dividend row with
    # the same symbol on the same date.
    WITHHOLDING_TAX = "withholding tax"


# Recognised but not CGT events, so they are dropped at
# parsing and never reach the timeline, the FX converter,
# or a processor. Stock plan activity is in the equity
# file; a wire sent is cash out. They are listed here only
# so they are recognised rather than warned about as
# unrecognised.
IGNORED_ACTIONS: frozenset[Action] = frozenset(
    {Action.STOCK_PLAN_ACTIVITY, Action.WIRE_SENT}
)


# Income actions: dividends, interest, and tax already
# withheld from either. One processor collects them all;
# the sums per tax year are made at reporting.
# Standing assumption: all of this tax is foreign tax
# withheld at source -- in practice US tax taken by
# Schwab, NRA withholding on a non-US person -- and none
# of it is HMRC tax. Revisit if another broker ever shows
# UK tax withheld.
INCOME_ACTIONS: frozenset[Action] = frozenset(
    {Action.DIVIDEND, Action.INTEREST, Action.WITHHOLDING_TAX}
)


# Global ordering within a single date. Earlier in the
# list means earlier in processing that day.
# Ignored actions are dropped at parsing, so they are not
# in this list. A new action is added to this list in the
# same chunk as its processor, never before.
ACTION_ORDER: list[Action] = [
    Action.SELL,
    Action.BUY,
    Action.DIVIDEND,
    Action.INTEREST,
    Action.WITHHOLDING_TAX,
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
    # Parser name, as in parsers.PARSERS. No default: a
    # provider without one has its files ignored, with a
    # warning per file, instead of being parsed as the
    # wrong format.
    parser: str | None = None


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
