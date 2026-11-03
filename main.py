#!/usr/bin/env python3
"""CLI entry point: parse, convert to GBP, build a global timeline, dispatch.

Layout, with --data-dir defaulting to "data":
  <data-dir>/input/<provider>/config.json   required per provider
  <data-dir>/input/<provider>/...           CSVs, any layout, recursive
  <data-dir>/output/                        reports (later steps)
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

    report(state, timeline)
    return 0


def report(state: State, timeline: list[Event]) -> None:
    # TODO: reporting not implemented yet.
    # Will include: portfolio holdings, per-ticker breakdown,
    # per-action breakdown, and per-tax-year gains, from
    # state.portfolio and state.matcher.gains.
    pass


if __name__ == "__main__":
    sys.exit(main())
