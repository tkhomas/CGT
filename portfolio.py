"""Portfolio: the state of assets at a moment in time.

Only pools grouped by ticker. Dividends and interest do not
change holdings, so they do not touch the portfolio; gains
and income totals live elsewhere, grouped per tax year in
reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class Pool:
    symbol: str
    units: int = 0
    total_cost: Decimal = Decimal(0)


@dataclass
class Portfolio:
    pools: dict[str, Pool] = field(default_factory=dict)

    def get_pool(self, symbol: str) -> Pool:
        if symbol not in self.pools:
            self.pools[symbol] = Pool(symbol=symbol)
        return self.pools[symbol]
