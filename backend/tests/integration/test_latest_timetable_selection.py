from scraper.scheduler import (
    _build_query,
    _build_week_query,
    _compact_items,
    _day_from_subject,
    _latest_messages_by_weekday,
    _summarize,
    run_once,
)
from scraper.timetable_parser import TIMETABLE_PARSER_VERSION


def test_weekday_query_matches_subject_and_body_variants_without_an_age_limit():
    query = _build_query('subject:"Class Schedule" in:inbox', "Tuesday")

    assert "{subject:Tuesday \"for Tuesday\"}" in query
    assert "newer_than:" not in query
    assert "-in:trash" in query
    assert '-subject:"Re:"' in query
    assert '-subject:"Fwd:"' in query


def test_latest_weekday_messages_use_independent_unbounded_queries(monkeypatch):
    calls = []

    def fake_list(_service, *, user_id, query, max_results):
        calls.append((user_id, query, max_results))
        if 'subject:Monday' in query:
            return [{"id": "new-monday"}]
        return [{"id": "older-latest-tuesday"}]

    monkeypatch.setattr("scraper.scheduler.list_messages", fake_list)

    result = _latest_messages_by_weekday(
        object(),
        "me",
        'subject:"Class Schedule" in:inbox',
        ["Monday", "Tuesday"],
    )

    assert result == {
        "Monday": {"id": "new-monday"},
        "Tuesday": {"id": "older-latest-tuesday"},
    }
    assert len(calls) == 2
    assert all(user_id == 'me' and max_results == 1 and 'newer_than:' not in query
               for user_id, query, max_results in calls)


def test_newer_weekday_emails_replace_only_older_emails_for_that_day(monkeypatch):
    inbox_newest_first = [
        ('Monday', 'new-monday'), ('Tuesday', 'new-tuesday'),
        ('Wednesday', 'wednesday'), ('Friday', 'friday'),
        ('Saturday', 'saturday'), ('Monday', 'old-monday'),
        ('Tuesday', 'old-tuesday'),
    ]

    def fake_list(_service, *, user_id, query, max_results):
        assert user_id == 'me' and max_results == 1
        day = next(day for day in ('Monday', 'Tuesday', 'Wednesday', 'Friday', 'Saturday')
                   if f'subject:{day}' in query)
        return [{'id': message_id} for name, message_id in inbox_newest_first if name == day][:1]

    monkeypatch.setattr('scraper.scheduler.list_messages', fake_list)
    latest = _latest_messages_by_weekday(
        object(), 'me', 'subject:"Class Schedule" in:inbox',
        ['Monday', 'Tuesday', 'Wednesday', 'Friday', 'Saturday'],
    )

    assert [latest[day]['id'] for day in ('Monday', 'Tuesday', 'Wednesday', 'Friday', 'Saturday')] == [
        'new-monday', 'new-tuesday', 'wednesday', 'friday', 'saturday',
    ]


def test_latest_reply_cannot_displace_the_original_weekday_schedule(monkeypatch):
    inbox_newest_first = [
        ('Re: Class Schedule – Wednesday, October 21st', 'cancellation-reply'),
        ('Class Schedule – Wednesday, October 21st', 'actual-wednesday-schedule'),
        ('Class Schedule – Wednesday, October 14th', 'older-wednesday-schedule'),
    ]

    def fake_list(_service, *, user_id, query, max_results):
        assert user_id == 'me' and max_results == 1
        assert '-subject:"Re:"' in query
        return [
            {'id': message_id}
            for subject, message_id in inbox_newest_first
            if subject.startswith('Class Schedule') and 'Wednesday' in subject
        ][:max_results]

    monkeypatch.setattr('scraper.scheduler.list_messages', fake_list)

    latest = _latest_messages_by_weekday(
        object(), 'me', 'subject:("Class Schedule" OR schedule) in:inbox', ['Wednesday'],
    )

    assert latest['Wednesday']['id'] == 'actual-wednesday-schedule'


def test_week_query_collects_each_supported_day_in_one_gmail_search():
    query = _build_week_query('subject:"Class Schedule" in:inbox')

    assert "subject:Monday" in query
    assert "subject:Saturday" in query
    assert '"for Tuesday"' in query
    assert "newer_than:" not in query


def test_day_detection_uses_whole_words_only():
    assert _day_from_subject("Class Schedule – Tuesday, September 15") == "Tuesday"
    assert _day_from_subject("Friday timetable") == "Friday"
    assert _day_from_subject("Fridayish draft") is None


def test_compact_items_remove_parser_diagnostics_but_keep_search_fields():
    item = {
        "row_number": 3,
        "semester": "BSSE7A",
        "semester_display": "BS(SE)-7A",
        "course": "SEC 3603 Software Project Management (3,0)",
        "course_title": "Software Project Management",
        "course_code": "SEC 3603",
        "faculty": "Muhammad Qasim",
        "room": "204",
        "time": "02:00 PM - 03:30 PM",
        "campus": "SZABIST University Campus",
        "schedule_day": "Monday",
        "raw_line": "very large parser source row",
        "raw_cells": ["duplicate", "parser", "data"],
        "full_text": "another duplicate parser representation",
        "faculty_name": "Muhammad Qasim",
    }

    compact = _compact_items([item])[0]

    assert compact["course"] == item["course"]
    assert compact["faculty"] == "Muhammad Qasim"
    assert compact["schedule_day"] == "Monday"
    assert "raw_line" not in compact
    assert "raw_cells" not in compact
    assert "full_text" not in compact
    assert "faculty_name" not in compact


def test_summary_uses_display_semesters_and_stable_course_identity():
    summary = _summarize([
        {"semester_display": "BS(SE)-7A", "course_code": "SEC 3603", "faculty": "One"},
        {"semester_display": "BS(SE)-7A", "course_code": "SEC 3603", "faculty": "Two"},
    ])

    assert summary == {
        "total_items": 2,
        "semester_breakdown": {"BS(SE)-7A": 2},
        "unique_courses": 1,
        "unique_faculty": 2,
    }


def test_week_refresh_fetches_and_parses_only_changed_weekdays(monkeypatch):
    from unittest.mock import MagicMock

    previous = {
        "snapshot_version": 1,
        "parser_version": TIMETABLE_PARSER_VERSION,
        "message_ids_by_day": {"Monday": "old-mon", "Tuesday": "same-tue"},
        "source_received_at_by_day": {"Tuesday": "2026-09-15T10:00:00Z"},
        "items": [{
            "schedule_day": "Tuesday",
            "semester_display": "BS(SE)-7A",
            "course_title": "Reused Course",
            "course_code": "SEC 1000",
            "time": "08:00 AM - 09:30 AM",
        }],
    }
    fake_store = MagicMock()
    fake_store.get_user_tokens.return_value = {"token": "token"}
    monkeypatch.setattr("database.firestore_store.data_store", fake_store)
    monkeypatch.setattr("scraper.scheduler.build_service", lambda _credentials: object())
    monkeypatch.setattr("scraper.scheduler._latest_messages_by_weekday", lambda *_args: {
        "Monday": {"id": "new-mon"}, "Tuesday": {"id": "same-tue"},
    })
    fetched_ids = []

    def fake_fetch(_service, _email, message_id):
        fetched_ids.append(message_id)
        return {"id": message_id, "internalDate": "1789999200000", "payload": {}}

    monkeypatch.setattr("scraper.scheduler.get_message", fake_fetch)
    monkeypatch.setattr("scraper.scheduler.get_message_html_from_message", lambda _message: "new html")
    monkeypatch.setattr("scraper.scheduler.parse_html_with_diagnostics", lambda *_args: ([{
        "semester_display": "BS(SE)-7A", "course_title": "New Course",
        "course_code": "SEC 2000", "time": "09:30 AM - 11:00 AM",
    }], {"parser_version": TIMETABLE_PARSER_VERSION, "accepted_rows": 1}))

    result = run_once(
        user_email="me",
        user_id="user-1",
        user_settings={"timetable_day": "Entire Week", "_save_cache": False, "_previous_source": previous},
    )

    assert result["success"] is True
    assert fetched_ids == ["new-mon"]
    assert result["data"]["refresh"] == {
        "changed_days": ["Monday"],
        "content_changed_days": ["Monday"],
        "reused_days": ["Tuesday"],
        "requested_days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"],
        "fetched_messages": 1,
    }
    assert {item["course_title"] for item in result["data"]["items"]} == {"New Course", "Reused Course"}


def test_legacy_partial_source_is_reparsed_as_a_full_week(monkeypatch):
    from unittest.mock import MagicMock

    previous = {
        "parser_version": TIMETABLE_PARSER_VERSION,
        "message_ids_by_day": {"Monday": "same-mon"},
        "items": [{"schedule_day": "Monday", "course_title": "Old Course"}],
    }
    fake_store = MagicMock()
    fake_store.get_user_tokens.return_value = {"token": "token"}
    monkeypatch.setattr("database.firestore_store.data_store", fake_store)
    monkeypatch.setattr("scraper.scheduler.build_service", lambda _credentials: object())
    monkeypatch.setattr("scraper.scheduler._latest_messages_by_weekday", lambda *_args: {
        "Monday": {"id": "same-mon"},
    })
    fetched = []

    def fake_get(_service, _email, message_id):
        fetched.append(message_id)
        return {"id": message_id, "payload": {}}

    monkeypatch.setattr("scraper.scheduler.get_message", fake_get)
    monkeypatch.setattr("scraper.scheduler.get_message_html_from_message", lambda _message: "html")
    monkeypatch.setattr("scraper.scheduler.parse_html_with_diagnostics", lambda *_args: (
        [{"course_title": "Reparsed Course"}], {"accepted_rows": 1},
    ))

    result = run_once(user_email="me", user_id="user-1", user_settings={
        "timetable_day": "Entire Week", "_save_cache": False,
        "_previous_source": previous,
    })

    assert result["success"] is True
    assert result["data"]["snapshot_version"] == 1
    assert fetched == ["same-mon"]
    assert result["data"]["items"][0]["course_title"] == "Reparsed Course"


def test_failed_newest_message_load_does_not_replace_saved_week(monkeypatch):
    from unittest.mock import MagicMock

    fake_store = MagicMock()
    fake_store.get_user_tokens.return_value = {"token": "token"}
    monkeypatch.setattr("database.firestore_store.data_store", fake_store)
    monkeypatch.setattr("scraper.scheduler.build_service", lambda _credentials: object())
    monkeypatch.setattr("scraper.scheduler._latest_messages_by_weekday", lambda *_args: {
        "Monday": {"id": "new-monday"},
    })
    monkeypatch.setattr("scraper.scheduler.get_message", lambda *_args: None)

    result = run_once(user_email="me", user_id="user-1", user_settings={
        "timetable_day": "Entire Week",
        "_previous_source": {
            "snapshot_version": 1,
            "parser_version": TIMETABLE_PARSER_VERSION,
            "message_ids_by_day": {"Monday": "old-monday"},
            "items": [{"schedule_day": "Monday", "course_title": "Old Course"}],
        },
    })

    assert result["success"] is False
    assert "could not be loaded" in result["error"]
    fake_store.save_timetable_cache.assert_not_called()


def test_targeted_refresh_keeps_other_weekdays_without_querying_them(monkeypatch):
    from unittest.mock import MagicMock

    previous = {
        "snapshot_version": 1,
        "parser_version": TIMETABLE_PARSER_VERSION,
        "message_ids_by_day": {"Monday": "same-mon", "Tuesday": "old-tue"},
        "items": [
            {"schedule_day": "Monday", "course_title": "Monday Course", "time": "08:00 AM - 09:30 AM"},
            {"schedule_day": "Tuesday", "course_title": "Tuesday Course", "time": "09:30 AM - 11:00 AM"},
        ],
    }
    fake_store = MagicMock()
    fake_store.get_user_tokens.return_value = {"token": "token"}
    monkeypatch.setattr("database.firestore_store.data_store", fake_store)
    monkeypatch.setattr("scraper.scheduler.build_service", lambda _credentials: object())
    requested = []

    def fake_latest(_service, _email, _query, days):
        requested.extend(days)
        return {"Monday": {"id": "same-mon"}}

    monkeypatch.setattr("scraper.scheduler._latest_messages_by_weekday", fake_latest)
    monkeypatch.setattr("scraper.scheduler.get_message", lambda *_args: {})

    result = run_once(
        user_email="me",
        user_id="user-1",
        user_settings={
            "timetable_day": "Entire Week",
            "_save_cache": False,
            "_previous_source": previous,
            "_refresh_days": ["Monday"],
        },
    )

    assert requested == ["Monday"]
    assert {item["course_title"] for item in result["data"]["items"]} == {
        "Monday Course", "Tuesday Course",
    }
    assert result["data"]["refresh"]["requested_days"] == ["Monday"]
    assert result["data"]["refresh"]["reused_days"] == ["Monday"]


def test_old_or_missing_email_never_gets_a_new_refresh_date(monkeypatch):
    from unittest.mock import MagicMock

    previous = {
        "snapshot_version": 1,
        "parser_version": TIMETABLE_PARSER_VERSION,
        "message_ids_by_day": {"Monday": "old-monday", "Tuesday": "old-tuesday"},
        "source_received_at_by_day": {
            "Monday": "2026-10-12T11:00:00Z",
            "Tuesday": "2026-10-13T11:00:00Z",
        },
        # These dates came from the old check-time behavior, not new emails.
        "refreshed_at_by_day": {
            "Monday": "2026-10-22T10:00:00Z",
            "Tuesday": "2026-10-22T10:00:00Z",
        },
        "items": [
            {"schedule_day": "Monday", "course_title": "Monday Course"},
            {"schedule_day": "Tuesday", "course_title": "Tuesday Course"},
        ],
    }
    fake_store = MagicMock()
    fake_store.get_user_tokens.return_value = {"token": "token"}
    monkeypatch.setattr("database.firestore_store.data_store", fake_store)
    monkeypatch.setattr("scraper.scheduler.build_service", lambda _credentials: object())
    monkeypatch.setattr("scraper.scheduler._latest_messages_by_weekday", lambda *_args: {
        "Monday": {"id": "old-monday"},
    })
    monkeypatch.setattr("scraper.scheduler.get_message", lambda *_args: {})

    result = run_once(
        user_email="me",
        user_id="user-1",
        user_settings={
            "timetable_day": "Entire Week",
            "_save_cache": False,
            "_previous_source": previous,
            "_refresh_days": ["Monday", "Tuesday"],
        },
    )

    assert result["success"] is True
    assert result["data"]["refreshed_at_by_day"] == {
        "Monday": "2026-10-12T11:00:00Z",
        "Tuesday": "2026-10-13T11:00:00Z",
    }
    assert {item["course_title"] for item in result["data"]["items"]} == {
        "Monday Course", "Tuesday Course",
    }
