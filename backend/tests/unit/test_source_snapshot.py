from core.source_snapshot import SUPPORTED_DAYS, has_cached_source, normalize_requested_days


def test_requested_days_are_normalized_in_timetable_order():
    assert normalize_requested_days(["tuesday", "MONDAY", "Sunday", "Monday"]) == [
        "Monday", "Tuesday",
    ]
    assert len(SUPPORTED_DAYS) == 6


def test_empty_week_is_still_a_valid_saved_snapshot():
    assert has_cached_source({"items": []})
    assert not has_cached_source({"items": None})
