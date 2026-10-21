"""Helpers for the durable, manually refreshed weekly timetable snapshot."""

from __future__ import annotations

from typing import Any, Iterable


SUPPORTED_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def normalize_requested_days(days: Iterable[str]) -> list[str]:
    selected = {str(day).strip().title() for day in days}
    return [day for day in SUPPORTED_DAYS if day in selected]


def has_cached_source(source: Any) -> bool:
    return isinstance(source, dict) and isinstance(source.get("items"), list)
