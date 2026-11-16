"""Symbol aliases: one holding under several spellings.

A rename is not a disposal: when a broker renames a
ticker, the shares are the same shares under a new name,
so the old and the new spelling are one holding, one
Section 104 pool. The aliases file says which spellings
those are, in the form the user writes it:

    {"ACME": ["ACME", "ACMX"]}

The key is how the holding is known from here on; the
list is the real spellings seen in the input files, the
key itself included. A symbol in no list is left alone.

The file lives at <data-dir>/input/aliases.json, next to
the provider folders, and applies to every provider. It
is optional: a missing file means no aliases. Anything
else wrong with it raises ValueError, loud, for the user
to fix -- the file is an instruction, not data to guess
at. In particular one spelling under two keys is a
contradiction, not a tie to break.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from models import ParsedEvent


def load_aliases(path: Path) -> dict[str, str]:
    """Load the aliases file as input spelling to canonical name.

    A missing file is no aliases, not an error.
    """
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise TypeError(f"{path}: expected an object of canonical name to spellings")
    aliases: dict[str, str] = {}
    for canonical, spellings in raw.items():
        canonical = canonical.strip()
        if not canonical:
            raise ValueError(f"{path}: empty canonical name")
        if not isinstance(spellings, list):
            raise TypeError(f"{path}: spellings for {canonical!r} must be a list")
        for spelling in spellings:
            if not isinstance(spelling, str) or not spelling.strip():
                raise ValueError(
                    f"{path}: spellings for {canonical!r} must be non-empty strings"
                )
            spelling = spelling.strip()
            if spelling in aliases and aliases[spelling] != canonical:
                raise ValueError(
                    f"{path}: {spelling!r} is listed under both "
                    f"{aliases[spelling]!r} and {canonical!r}"
                )
            aliases[spelling] = canonical
    return aliases


def canonicalise_events(
    events: list[ParsedEvent], aliases: dict[str, str]
) -> list[ParsedEvent]:
    """Rewrite each event's symbol to its canonical name.

    A symbol in no alias list is left alone. Returns new
    events; the parsed ones are untouched.
    """
    if not aliases:
        return list(events)
    return [
        replace(event, symbol=aliases.get(event.symbol, event.symbol))
        for event in events
    ]
