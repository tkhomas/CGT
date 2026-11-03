"""Reporting package.

All report generation lives here, not in main.py.
main.py only calls into it with the finished state.
"""

from reporting.html import (
    build_disposals_report,
    build_portfolio_report,
    write_disposals_report,
    write_portfolio_report,
)

__all__ = [
    "build_disposals_report",
    "build_portfolio_report",
    "write_disposals_report",
    "write_portfolio_report",
]
