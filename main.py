#!/usr/bin/env python3
"""CLI entry point: parse, build a global timeline, dispatch to processors.

Layout, with --data-dir defaulting to "data":
  <data-dir>/input/<provider>/config.json   required per provider
  <data-dir>/input/<provider>/...           CSVs, any layout, recursive
  <data-dir>/output/                        reports (later steps)
  <data-dir>/output/cache/                  internal cache, not a CLI option
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from models import Event
from parsing import parse_csv
from portfolio import Portfolio
from processors import dispatch
from providers import find_inputs
from timeline import build_global_timeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cgt")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default=None)
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

    all_events = []
    total_skipped = 0
    for inp in inputs:
        events, skipped = parse_csv(inp.path, inp.provider)
        all_events.extend(events)
        total_skipped += skipped

    timeline = build_global_timeline(all_events)
    portfolio = Portfolio()
    for event in timeline:
        dispatch(event, portfolio)

    report(portfolio, timeline)
    return 0


def report(portfolio: Portfolio, timeline: list[Event]) -> None:
    # TODO: reporting not implemented yet.
    # Will include: portfolio holdings, per-ticker breakdown,
    # per-action breakdown, and per-tax-year gains.
    pass


if __name__ == "__main__":
    sys.exit(main())
