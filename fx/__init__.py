"""FX conversion package.

Re-exported here so main.py does not change when the
fx files move: `from fx import FxConverter` still works.
The real code lives in fx/converter.py and fx/providers.py.
"""

from fx.converter import FxConverter, FxRateMissingError
from fx.providers import (
    FxProvider,
    HmrcXmlProvider,
    TradeTariffProvider,
    UserProvidedRatesProvider,
    UserRateNotFoundError,
)

__all__ = [
    "FxConverter",
    "FxProvider",
    "FxRateMissingError",
    "HmrcXmlProvider",
    "TradeTariffProvider",
    "UserProvidedRatesProvider",
    "UserRateNotFoundError",
]
