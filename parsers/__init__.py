"""Parsers, one per file format.

Every parser takes one file and its provider config and
returns the same thing -- a list of ParsedEvents and a
count of skipped rows -- so FX, the timeline, the matcher
and the reports never know which format a buy came from.
A new format means a new module and one line in PARSERS.
"""

from __future__ import annotations

from pathlib import Path

from models import ParsedEvent, ProviderConfig
from parsers import schwab_equity, schwab_personal

PARSERS = {
    "schwab_personal": schwab_personal.parse_csv,
    "schwab_equity": schwab_equity.parse_csv,
}


def known_parser(name: str) -> bool:
    return name in PARSERS


def parse_file(path: Path, provider: ProviderConfig) -> tuple[list[ParsedEvent], int]:
    """Parse one file with the parser its provider names."""
    if provider.parser is None or provider.parser not in PARSERS:
        # Unreachable through main: find_inputs only passes
        # files whose provider names a known parser.
        raise ValueError(f"no parser {provider.parser!r} for {path}")
    return PARSERS[provider.parser](path, provider)
