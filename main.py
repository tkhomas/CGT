#!/usr/bin/env python3
"""CLI entry point: parse, convert to GBP, build a global timeline, dispatch.

Layout, with --data-dir defaulting to "data":
  <data-dir>/input/<provider>/config.json   required per provider
  <data-dir>/input/<provider>/...           CSVs, any layout, recursive
  <data-dir>/output/                        reports, one pair per tax year
  <data-dir>/output/portfolio-<year>.html   portfolio report for a tax year
  <data-dir>/output/disposals-<year>.html   disposals report for a tax year
  A tax year that is not final gets _CURRENT before .html.
  <data-dir>/output/cache/                  internal cache, not a CLI option
  <data-dir>/fx/user-provided.json          single file, all manual FX rates,
                                            ideally empty [], last resort only

  To bypass the cached FX files and redownload them, run:
    python main.py --force-fx-download
  A cached file is then ignored, and the new download replaces
  it only on success. Every download prints its URL and destination.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fx import FxConverter
from matching import PoolInsufficientError, State
from models import Event
from parsing import parse_csv
from processors import dispatch
from providers import find_inputs
from reporting.html import (
    write_disposals_report_year,
    write_portfolio_report_year,
)
from reporting.tax_year import (
    disposals_for_year,
    events_for_year,
    holdings_at_year_end,
    is_current,
    tax_years_for,
)
from timeline import build_global_timeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgt")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--force-fx-download",
        action="store_true",
        help="Ignore cached FX files and redownload them.",
    )
    parser.add_argument(
        "--clean-output",
        action="store_true",
        help="Delete existing report files in the output dir before "
        "writing new ones. Only report files are deleted: "
        "portfolio-*.html, disposals-*.html, and the old report.html. "
        "Cache, inputs, and anything else are left alone. "
        "Cleaning happens only after a successful run, so a "
        "failed run never deletes the previous reports.",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir) if args.output_dir else data_dir / "output"
    cache_dir = output_dir / "cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.is_dir():
        print(f"Error: data dir not found: {data_dir}", file=sys.stderr)
        return 2

    inputs = find_inputs(data_dir)
    if inputs is None:
        return 2

    all_parsed = []
    total_skipped = 0
    for inp in inputs:
        events, skipped = parse_csv(inp.path, inp.provider)
        all_parsed.extend(events)
        total_skipped += skipped

    user_path = data_dir / "fx" / "user-provided.json"
    try:
        with FxConverter(cache_dir, user_path, args.force_fx_download) as fx:
            all_events = fx.convert_events(all_parsed)
    except (RuntimeError, ValueError, TypeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    timeline = build_global_timeline(all_events)
    state = State()
    try:
        for event in timeline:
            dispatch(event, state)
        state.matcher.finish(state.portfolio)
    except PoolInsufficientError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        pool = state.portfolio.pools.get(exc.symbol)
        if pool is not None:
            print(
                f"Debug: pool for {exc.symbol} at failure: "
                f"{pool.units} units, total cost {pool.total_cost}",
                file=sys.stderr,
            )
        print(f"Debug: all events for {exc.symbol}:", file=sys.stderr)
        for event in timeline:
            if event.symbol == exc.symbol:
                print(
                    f"  {event.date} {event.action.value} "
                    f"{event.quantity} @ {event.pricing.price} "
                    f"fees {event.pricing.fees} amount {event.pricing.amount} "
                    f"{event.pricing.currency.value} "
                    f"(orig {event.pricing.original_price} "
                    f"{event.pricing.original_currency.value} @ "
                    f"{event.pricing.original_units_per_gbp} "
                    f"{event.pricing.fx_source}) "
                    f"{event.provider} "
                    f"{event.source_file}:{event.row}",
                    file=sys.stderr,
                )
        return 2
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.clean_output:
        cleaned = clean_reports(output_dir)
        print(f"Cleaned {cleaned} old report(s) from {output_dir}")

    all_disposals = state.matcher.gains.disposals
    tax_years = tax_years_for(timeline, all_disposals)
    if not tax_years:
        print("No events, no reports written.")
        return 0
    latest_date = max(event.date for event in timeline)
    for tax_year in tax_years:
        year_disposals = disposals_for_year(all_disposals, tax_year)
        year_events, has_future_buys = events_for_year(
            timeline, year_disposals, tax_year
        )
        year_holdings = holdings_at_year_end(timeline, all_disposals, tax_year.end)
        current = is_current(tax_year, latest_date)
        suffix = "_CURRENT" if current else ""
        portfolio_path = write_portfolio_report_year(
            year_holdings,
            year_events,
            tax_year,
            current,
            has_future_buys,
            output_dir / f"portfolio-{tax_year.file_label}{suffix}.html",
        )
        disposals_path = write_disposals_report_year(
            year_disposals,
            year_events,
            tax_year,
            current,
            has_future_buys,
            output_dir / f"disposals-{tax_year.file_label}{suffix}.html",
        )
        print(f"Portfolio report written to {portfolio_path}")
        print(f"Disposals report written to {disposals_path}")
    return 0


def clean_reports(output_dir: Path) -> int:
    # Report files only, never cache or inputs. Patterns are
    # the ones this program writes, plus the old single
    # report.html, which would otherwise go stale.
    cleaned = 0
    for pattern in ("portfolio-*.html", "disposals-*.html", "report.html"):
        for path in output_dir.glob(pattern):
            if path.is_file():
                path.unlink()
                cleaned += 1
    return cleaned


if __name__ == "__main__":
    sys.exit(main())
