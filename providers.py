"""Provider discovery: config loading and input file finding."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from models import Currency, ProviderConfig, ProviderInput
from parsers import known_parser

CONFIG_FILENAME = "config.json"


def load_provider_config(provider_dir: Path) -> ProviderConfig | None:
    """Load a provider config file, or None with a warning."""
    config_path = provider_dir / CONFIG_FILENAME
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(
            f"Warning: provider {provider_dir.name}: "
            f"cannot read {CONFIG_FILENAME} ({exc}), provider skipped",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"Warning: provider {provider_dir.name}: "
            f"{CONFIG_FILENAME} is not an object, provider skipped",
            file=sys.stderr,
        )
        return None
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        print(
            f"Warning: provider {provider_dir.name}: "
            f"{CONFIG_FILENAME} missing name, provider skipped",
            file=sys.stderr,
        )
        return None
    date_format = data.get("date_format")
    if not isinstance(date_format, str) or not date_format:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"{CONFIG_FILENAME} missing date_format, provider skipped",
            file=sys.stderr,
        )
        return None
    try:
        currency = Currency(data.get("currency"))
    except ValueError:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"{CONFIG_FILENAME} has unknown currency "
            f"{data.get('currency')!r}, provider skipped",
            file=sys.stderr,
        )
        return None
    # The parser is read here but not validated: a provider
    # without a usable parser is not skipped, its files are
    # ignored one by one in find_inputs, with a warning per
    # file, so it is clear what was left out.
    parser_name = data.get("parser")
    if parser_name is not None and not isinstance(parser_name, str):
        print(
            f"Warning: provider {provider_dir.name}: "
            f"{CONFIG_FILENAME} has a non-string parser, "
            f"its files will be ignored",
            file=sys.stderr,
        )
        parser_name = None
    return ProviderConfig(
        name=name.strip(),
        date_format=date_format,
        currency=currency,
        parser=parser_name,
    )


def find_inputs(data_dir: Path) -> list[ProviderInput] | None:
    """Collect CSVs, one provider folder at a time.

    Config files are skipped silently. Other non-CSV files
    warn and are skipped, they do not stop the run.
    """
    input_dir = data_dir / "input"
    if not input_dir.is_dir():
        print(f"Error: input dir not found: {input_dir}", file=sys.stderr)
        return None

    inputs = []
    seen_names = set()
    for entry in sorted(input_dir.iterdir()):
        if entry.is_file() and not entry.name.startswith("."):
            print(
                f"Warning: {entry.name} directly under input/, "
                f"expected a provider folder, skipped",
                file=sys.stderr,
            )

    provider_dirs = [
        p
        for p in sorted(input_dir.iterdir())
        if p.is_dir() and not p.name.startswith(".")
    ]
    for provider_dir in provider_dirs:
        config_path = provider_dir / CONFIG_FILENAME
        if not config_path.is_file():
            print(
                f"Warning: provider {provider_dir.name}: "
                f"no {CONFIG_FILENAME}, provider skipped",
                file=sys.stderr,
            )
            continue
        provider = load_provider_config(provider_dir)
        if provider is None:
            continue
        if provider.name in seen_names:
            print(
                f"Warning: provider {provider_dir.name}: "
                f"duplicate provider name {provider.name!r}, "
                f"provider skipped",
                file=sys.stderr,
            )
            continue
        seen_names.add(provider.name)
        files = sorted(
            p
            for p in provider_dir.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
        for path in files:
            if path.name == CONFIG_FILENAME:
                continue
            if path.suffix.lower() != ".csv":
                print(
                    f"Warning: {path} is not a CSV or config file, skipped",
                    file=sys.stderr,
                )
                continue
            if provider.parser is None:
                print(
                    f"Warning: {path}: no parser configured for "
                    f"provider {provider.name!r}, file ignored "
                    f'(add "parser" to {CONFIG_FILENAME})',
                    file=sys.stderr,
                )
                continue
            if not known_parser(provider.parser):
                print(
                    f"Warning: {path}: unknown parser "
                    f"{provider.parser!r} for provider "
                    f"{provider.name!r}, file ignored",
                    file=sys.stderr,
                )
                continue
            inputs.append(ProviderInput(path=path, provider=provider))

    if not inputs:
        print(f"Error: no CSV found under {input_dir}", file=sys.stderr)
        return None
    return inputs
