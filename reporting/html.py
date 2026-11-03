"""HTML reports, self-contained: inline CSS, no external assets, no JS.

Two files, both with all events, no filtering:

- portfolio.html: holdings (Section 104 pools, units and cost,
  so units per stock are easy to verify) plus all events,
  including internal source file references.
- disposals.html: summary totals, disposals, plus all events
  since the start, in chronological order. No internal file
  names here: they mean nothing to HMRC. FX rates instead
  link to the actual monthly source file they came from.

Display only: amounts are shown to the penny. Calculations
in the matcher use unrounded Decimal values, so a displayed
row can differ by a penny from a re-addition of displayed rows.
"""

from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from fx.providers import HmrcXmlProvider, TradeTariffProvider
from matching import State
from models import Event


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


def _holdings_rows(state: State) -> str:
    pools = sorted(state.portfolio.pools.values(), key=lambda p: p.symbol)
    if not pools:
        return "<tr><td colspan='3'>No holdings.</td></tr>"
    rows = []
    for pool in pools:
        rows.append(
            f"<tr><td>{_esc(pool.symbol)}</td>"
            f"<td class='num'>{pool.units}</td>"
            f"<td class='num'>{_gbp(pool.total_cost)}</td></tr>"
        )
    return "\n".join(rows)


def _disposals_rows(state: State) -> str:
    disposals = state.matcher.gains.disposals
    if not disposals:
        return "<tr><td colspan='8'>No disposals.</td></tr>"
    rows = []
    for disposal in disposals:
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


def _page(title: str, heading: str, note: str, body: str) -> str:
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
<p class="note">{note}</p>
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


def build_portfolio_report(state: State, timeline: list[Event]) -> str:
    body = f"""<h2>Holdings</h2>
<table>
<tr><th>Symbol</th><th>Units</th><th>Total cost</th></tr>
{_holdings_rows(state)}
</table>

<h2>All events</h2>
{_events_table(timeline, internal_source=True)}

{_methodology_html()}
"""
    return _page(
        "Portfolio report",
        "Portfolio report",
        "Holdings are Section 104 pools: units and total cost per stock, "
        "so units for any stock are easy to verify. All events follow, "
        "in chronological order, with internal source references.",
        body,
    )


def build_disposals_report(state: State, timeline: list[Event]) -> str:
    gains = state.matcher.gains
    body = f"""<h2>Summary</h2>
<table>
<tr><th>Total proceeds</th><td class='num'>{_gbp(gains.total_proceeds)}</td></tr>
<tr><th>Total cost</th><td class='num'>{_gbp(gains.total_cost)}</td></tr>
<tr><th>Total gain</th><td class='num'>{_gbp(gains.total_gain)}</td></tr>
<tr><th>Disposals</th><td class='num'>{len(gains.disposals)}</td></tr>
</table>

<h2>Disposals</h2>
<table>
<tr><th>Sell date</th><th>Symbol</th><th>Units</th><th>Proceeds</th>
<th>Cost</th><th>Gain</th><th>Match type</th><th>Buy date</th></tr>
{_disposals_rows(state)}
</table>

<h2>All events</h2>
{_events_table(timeline, internal_source=False)}

{_methodology_html()}
"""
    return _page(
        "Disposals report",
        "Disposals report",
        "All amounts in GBP, shown to the penny; calculations use "
        "unrounded Decimal values. Gains are global for now, no tax-year "
        "split yet. All events follow, since the start, in chronological "
        "order, with FX rates linked to their monthly source files. "
        "No local file names are included.",
        body,
    )


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_portfolio_report(state: State, timeline: list[Event], path: Path) -> Path:
    return _write(path, build_portfolio_report(state, timeline))


def write_disposals_report(state: State, timeline: list[Event], path: Path) -> Path:
    return _write(path, build_disposals_report(state, timeline))
