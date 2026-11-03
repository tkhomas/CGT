"""HTML reports, self-contained: inline CSS, no external assets, no JS.

Two files per UK tax year, both with all events, no filtering:

- portfolio-<year>.html: holdings at the end of that tax
  year (Section 104 pools, units and cost, so units per
  stock are easy to verify) plus all events, including
  internal source file references.
- disposals-<year>.html: summary totals and disposals for
  that tax year only, plus all events since the start, in
  chronological order. No internal file names here: they
  mean nothing to HMRC. FX rates instead link to the
  actual monthly source file they came from.

A tax year that is not final gets _CURRENT in its file
name and a banner in the report: do not submit it yet.
See reporting/tax_year.py for what makes a year final.

Display only: amounts are shown to the penny. Calculations
in the matcher use unrounded Decimal values, so a displayed
row can differ by a penny from a re-addition of displayed rows.
"""

from __future__ import annotations

import html
from datetime import date
from decimal import Decimal
from pathlib import Path

from fx.providers import HmrcXmlProvider, TradeTariffProvider
from matching import Disposal
from models import Event
from reporting.tax_year import TaxYear


def _gbp(value: Decimal) -> str:
    # £1,000.00, -£123.45 for negatives: sign before the symbol.
    if value < 0:
        return f"-£{-value:,.2f}"
    return f"£{value:,.2f}"


def _plain(value: Decimal) -> str:
    # Exact Decimal in plain notation, no scientific form.
    return format(value, "f")


def _esc(value: object) -> str:
    return html.escape(str(value))


def _fx_url(fx_source: str) -> str | None:
    # Reuse the providers' own download URLs, so a URL change
    # in a provider is picked up here automatically. The first
    # URL is the one the downloader tries first. GBP and
    # user-provided rates have no source URL.
    try:
        kind, year_month = fx_source.split(":", 1)
        year_s, month_s = year_month.split("-")
        year, month = int(year_s), int(month_s)
    except ValueError:
        return None
    if kind == "hmrc-xml":
        urls = HmrcXmlProvider(Path()).download_urls(year, month)
    elif kind == "trade-tariff":
        urls = TradeTariffProvider(Path()).download_urls(year, month)
    else:
        return None
    return urls[0] if urls else None


def _fx_cell(fx_source: str) -> str:
    # Human source name, not the internal label and not a file
    # name: "hmrc-xml:2019-01" is what leaked file-like text
    # into the disposals report. The URL itself is still the
    # monthly source file, that is the audit link, but it
    # lives in the href, not in the visible text.
    if fx_source == "GBP":
        return "GBP, no conversion"
    try:
        kind, year_month = fx_source.split(":", 1)
        year_s, month_s = year_month.split("-")
        year, month = int(year_s), int(month_s)
    except ValueError:
        return _esc(fx_source)
    label = {
        "hmrc-xml": "HMRC monthly rates",
        "trade-tariff": "Trade Tariff monthly rates",
        "user-provided": "User-provided rate",
    }.get(kind)
    if label is None:
        return _esc(fx_source)
    text = f"{label}, {year}-{month:02d}"
    url = _fx_url(fx_source)
    if url is None:
        return _esc(text)
    return f'<a href="{_esc(url)}">{_esc(text)}</a>'


def _holdings_rows(holdings: dict[str, tuple[int, Decimal]]) -> str:
    if not holdings:
        return "<tr><td colspan='3'>No holdings.</td></tr>"
    rows = []
    for symbol, (units, total_cost) in sorted(holdings.items()):
        rows.append(
            f"<tr><td>{_esc(symbol)}</td>"
            f"<td class='num'>{units}</td>"
            f"<td class='num'>{_gbp(total_cost)}</td></tr>"
        )
    return "\n".join(rows)


def _disposals_rows(disposals: list[Disposal]) -> str:
    if not disposals:
        return "<tr><td colspan='8'>No disposals.</td></tr>"
    # The matcher appends disposals in resolution order, not
    # sell-date order: a bed-and-breakfast disposal only exists
    # when its buy arrives, a pool disposal only when its queue
    # ages out. The report is read by sell date, so sort here,
    # in reporting, not in the matcher. Same sell date and
    # symbol: same-day, then bed-and-breakfast, then pool,
    # then by buy date. Totals are unaffected by the order.
    match_order = {"same-day": 0, "bed-and-breakfast": 1, "pool": 2}
    ordered = sorted(
        disposals,
        key=lambda d: (
            d.sell_date,
            d.symbol,
            match_order[d.match_type.value],
            d.buy_date or date.min,
        ),
    )
    rows = []
    for disposal in ordered:
        buy_date = disposal.buy_date.isoformat() if disposal.buy_date else "—"
        rows.append(
            f"<tr><td>{disposal.sell_date.isoformat()}</td>"
            f"<td>{_esc(disposal.symbol)}</td>"
            f"<td class='num'>{disposal.units}</td>"
            f"<td class='num'>{_gbp(disposal.proceeds)}</td>"
            f"<td class='num'>{_gbp(disposal.cost)}</td>"
            f"<td class='num'>{_gbp(disposal.gain)}</td>"
            f"<td>{_esc(disposal.match_type.value)}</td>"
            f"<td>{buy_date}</td></tr>"
        )
    return "\n".join(rows)


def _events_rows(timeline: list[Event], internal_source: bool) -> str:
    # internal_source False: broker name only, no file names.
    # internal_source True: broker name plus file and row.
    colspan = 12 if internal_source else 11
    if not timeline:
        return f"<tr><td colspan='{colspan}'>No events.</td></tr>"
    rows = []
    for event in timeline:
        pricing = event.pricing
        row = (
            f"<tr><td>{event.date.isoformat()}</td>"
            f"<td>{_esc(event.action.value)}</td>"
            f"<td>{_esc(event.symbol)}</td>"
            f"<td class='num'>{event.quantity}</td>"
            f"<td class='num'>{_plain(pricing.original_price)} "
            f"{_esc(pricing.original_currency.value)}</td>"
            f"<td class='num'>{_plain(pricing.original_amount)} "
            f"{_esc(pricing.original_currency.value)}</td>"
            f"<td class='num'>{_plain(pricing.original_units_per_gbp)} "
            f"{_esc(pricing.original_currency.value)} per £1</td>"
            f"<td>{_fx_cell(pricing.fx_source)}</td>"
            f"<td class='num'>{_gbp(pricing.price)}</td>"
            f"<td class='num'>{_gbp(pricing.amount)}</td>"
            f"<td>{_esc(event.provider)}</td>"
        )
        if internal_source:
            row += f"<td>{_esc(event.source_file)}:{event.row}</td>"
        row += "</tr>"
        rows.append(row)
    return "\n".join(rows)


def _events_table(timeline: list[Event], internal_source: bool) -> str:
    header = (
        "<tr><th>Date</th><th>Action</th><th>Symbol</th><th>Qty</th>"
        "<th>Original price</th><th>Original amount</th><th>FX rate</th>"
        "<th>FX source</th><th>GBP price</th><th>GBP amount</th><th>Broker</th>"
    )
    if internal_source:
        header += "<th>Internal source</th>"
    header += "</tr>"
    return (
        "<table>\n"
        + header
        + "\n"
        + _events_rows(timeline, internal_source)
        + "\n</table>"
    )


def _page(
    title: str, heading: str, note: str, body: str, is_current: bool = False
) -> str:
    banner = ""
    if is_current:
        banner = (
            "<p class='note'><strong>CURRENT — unfinished tax year, "
            "do not submit yet.</strong> Figures can still change: "
            "more events can occur in this year, and a sale in its "
            "last 30 days can still be matched by a later buy.</p>\n"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #222; }}
table {{ border-collapse: collapse; margin: 1rem 0 2rem; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.8rem; text-align: left; }}
th {{ background: #eee; }}
.num {{ text-align: right; }}
.note {{ color: #555; }}
</style>
</head>
<body>
<h1>{_esc(heading)}</h1>
<p class="note">Generated with the
<a href="https://github.com/tkhomas/CGT">CGT calculator</a>,
whose code is open source and can be audited.</p>
{banner}<p class="note">{note}</p>
{body}
</body>
</html>
"""


def _methodology_html() -> str:
    # Only the general, auditable part of the old preamble:
    # the matching rules and the currency rule, with official
    # links. Left out deliberately: any broker-specific price
    # source, the DEPRECATED method, and the date-format note,
    # which is per-provider config, not methodology.
    return """<h2>Methodology</h2>
<p>Shares are held in a Section 104 pool per stock, at average
cost. Adding shares to the pool recalculates that average cost.</p>
<p>On a sale, shares are matched in this order:</p>
<ol>
<li>Shares acquired on the same day as the sale.</li>
<li>Shares acquired within 30 days after the sale
(bed-and-breakfasting rule).</li>
<li>Shares from the Section 104 pool.</li>
</ol>
<p>Currency: all amounts are converted to GBP before matching,
using the HMRC monthly exchange rate for the month of the
transaction, as published in original currency units per £1.
GBP amount = original amount / rate. Original amounts, rates,
and source links are shown in the events table above, so every
figure is shown in both currencies.</p>
<p>Sources:
<a href="https://www.gov.uk/government/publications/shares-and-capital-gains-tax-hs284-self-assessment-helpsheet/hs284-shares-and-capital-gains-tax-2021#how-you-work-out-the-gain-under-the-bed-and-breakfasting-rule">HS284 — Shares and Capital Gains Tax</a>,
<a href="https://www.gov.uk/hmrc-internal-manuals/capital-gains-manual">Capital Gains Manual</a>,
<a href="https://www.trade-tariff.service.gov.uk/exchange_rates">HMRC exchange rates</a>.</p>
"""


def build_portfolio_report_year(
    holdings: dict[str, tuple[int, Decimal]],
    events: list[Event],
    tax_year: TaxYear,
    is_current: bool,
    has_future_buys: bool,
) -> str:
    future_note = (
        " Buys after year end that match a disposal in this year "
        "are shown in the events below for audit only; they are "
        "not counted in these holdings."
        if has_future_buys
        else ""
    )
    body = f"""<h2>Holdings at end of {tax_year.label}</h2>
<table>
<tr><th>Symbol</th><th>Units</th><th>Total cost</th></tr>
{_holdings_rows(holdings)}
</table>

<h2>All events</h2>
{_events_table(events, internal_source=True)}

{_methodology_html()}
"""
    return _page(
        f"Portfolio report {tax_year.label}",
        f"Portfolio report {tax_year.label}",
        f"Tax year {tax_year.label}: 6 April {tax_year.start_year} to "
        f"5 April {tax_year.end_year}. Holdings are Section 104 pools: "
        "units and total cost per stock, so units for any stock are "
        "easy to verify. All events follow, in chronological order, "
        f"with internal source references.{future_note}",
        body,
        is_current=is_current,
    )


def build_disposals_report_year(
    disposals: list[Disposal],
    events: list[Event],
    tax_year: TaxYear,
    is_current: bool,
    has_future_buys: bool,
) -> str:
    total_proceeds = sum((d.proceeds for d in disposals), Decimal(0))
    total_cost = sum((d.cost for d in disposals), Decimal(0))
    total_gain = sum((d.gain for d in disposals), Decimal(0))
    future_note = (
        " Buys after year end that match a disposal in this year "
        "are shown in the events below for audit only."
        if has_future_buys
        else ""
    )
    body = f"""<h2>Summary for {tax_year.label}</h2>
<table>
<tr><th>Total proceeds</th><td class='num'>{_gbp(total_proceeds)}</td></tr>
<tr><th>Total cost</th><td class='num'>{_gbp(total_cost)}</td></tr>
<tr><th>Total gain</th><td class='num'>{_gbp(total_gain)}</td></tr>
<tr><th>Disposals</th><td class='num'>{len(disposals)}</td></tr>
</table>

<h2>Disposals in {tax_year.label}</h2>
<table>
<tr><th>Sell date</th><th>Symbol</th><th>Units</th><th>Proceeds</th>
<th>Cost</th><th>Gain</th><th>Match type</th><th>Buy date</th></tr>
{_disposals_rows(disposals)}
</table>

<h2>All events</h2>
{_events_table(events, internal_source=False)}

{_methodology_html()}
"""
    return _page(
        f"Disposals report {tax_year.label}",
        f"Disposals report {tax_year.label}",
        f"Tax year {tax_year.label}: 6 April {tax_year.start_year} to "
        "5 April "
        f"{tax_year.end_year}. All amounts in GBP, shown to the penny; "
        "calculations use unrounded Decimal values. Only disposals "
        "with a sell date in this tax year are listed above. All "
        "events follow, since the start, in chronological order, "
        "with FX rates linked to their monthly source files. "
        f"No local file names are included.{future_note}",
        body,
        is_current=is_current,
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_portfolio_report_year(
    holdings: dict[str, tuple[int, Decimal]],
    events: list[Event],
    tax_year: TaxYear,
    is_current: bool,
    has_future_buys: bool,
    path: Path,
) -> Path:
    return _write(
        path,
        build_portfolio_report_year(
            holdings, events, tax_year, is_current, has_future_buys
        ),
    )


def write_disposals_report_year(
    disposals: list[Disposal],
    events: list[Event],
    tax_year: TaxYear,
    is_current: bool,
    has_future_buys: bool,
    path: Path,
) -> Path:
    return _write(
        path,
        build_disposals_report_year(
            disposals, events, tax_year, is_current, has_future_buys
        ),
    )
