"""Provider discovery: config loading and input file finding."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from models import Currency, ProviderConfig, ProviderInput


def load_provider_config(provider_dir: Path) -> ProviderConfig | None:
    """Load <provider>/config.json, or None with a warning."""
    config_path = provider_dir / "config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"cannot read config.json ({exc}), provider skipped",
            file=sys.stderr,
        )
        return None
    if not isinstance(data, dict):
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json is not an object, provider skipped",
            file=sys.stderr,
        )
        return None
    date_format = data.get("date_format")
    if not isinstance(date_format, str) or not date_format:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json missing date_format, provider skipped",
            file=sys.stderr,
        )
        return None
    try:
        currency = Currency(data.get("currency"))
    except ValueError:
        print(
            f"Warning: provider {provider_dir.name}: "
            f"config.json has unknown currency "
            f"{data.get('currency')!r}, provider skipped",
            file=sys.stderr,
        )
        return None
    return ProviderConfig(
        name=provider_dir.name,
        date_format=date_format,
        currency=currency,
    )


def find_inputs(data_dir: Path) -> list[ProviderInput] | None:
    """Collect CSVs, one provider folder at a time.

    config.json files are skipped silently. Other non-CSV files
    warn and are skipped, they do not stop the run.
    """
    input_dir = data_dir / "input"
    if not input_dir.is_dir():
        print(f"Error: input dir not found: {input_dir}", file=sys.stderr)
        return None

    inputs = []
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
        config_path = provider_dir / "config.json"
        if not config_path.is_file():
            print(
                f"Warning: provider {provider_dir.name}: "
                f"no config.json, provider skipped",
                file=sys.stderr,
            )
            continue
        provider = load_provider_config(provider_dir)
        if provider is None:
            continue
        files = sorted(
            p
            for p in provider_dir.rglob("*")
            if p.is_file() and not p.name.startswith(".")
        )
        for path in files:
            if path.name == "config.json":
                continue  # config, not data, no warning
            if path.suffix.lower() == ".csv":
                inputs.append(ProviderInput(path=path, provider=provider))
            else:
                print(
                    f"Warning: {path} is not a CSV or config file, skipped",
                    file=sys.stderr,
                )

    if not inputs:
        print(f"Error: no CSV found under {input_dir}", file=sys.stderr)
        return None
    return inputs
