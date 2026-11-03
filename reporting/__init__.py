"""Reporting package.

All report generation lives here, not in main.py.
main.py only calls into it with the finished state.
"""

from reporting.html import (
    build_disposals_report_year,
    build_portfolio_report_year,
    write_disposals_report_year,
    write_portfolio_report_year,
)
from reporting.tax_year import (
    TaxYear,
    disposals_for_year,
    events_for_year,
    holdings_at_year_end,
    is_current,
    tax_year_of,
    tax_years_for,
)

__all__ = [
    "TaxYear",
    "build_disposals_report_year",
    "build_portfolio_report_year",
    "disposals_for_year",
    "events_for_year",
    "holdings_at_year_end",
    "is_current",
    "tax_year_of",
    "tax_years_for",
    "write_disposals_report_year",
    "write_portfolio_report_year",
]
