"""Global timeline: all events, sorted by date then action order."""

from __future__ import annotations

from models import ACTION_ORDER, Event


def build_global_timeline(events: list[Event]) -> list[Event]:
    """Sort by date, then position in ACTION_ORDER.

    Sorting is stable, so events with the same date and action
    keep their input order: provider folders sorted, files sorted,
    row order within a file.
    """
    return sorted(
        events,
        key=lambda event: (
            event.date,
            ACTION_ORDER.index(event.action)
            if event.action in ACTION_ORDER
            else len(ACTION_ORDER),
        ),
    )


def group_by_ticker(timeline: list[Event]) -> dict[str, list[Event]]:
    """Per-ticker views, derived from the global timeline.

    For reporting and audit only. Processing uses the global
    timeline, not these per-ticker lists.
    """
    timelines: dict[str, list[Event]] = {}
    for event in timeline:
        timelines.setdefault(event.symbol, []).append(event)
    return dict(sorted(timelines.items()))
