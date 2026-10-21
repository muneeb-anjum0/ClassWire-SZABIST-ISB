from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from core.ttl_cache import TTLCache
from database.firestore_store import (
    FirestoreStore,
    _cache_content_hash,
    _decode_json_payload,
    _encode_json_payload,
    _normalize_search_history,
)


def test_search_history_is_compact_deduplicated_and_account_safe():
    searches = ["  BSSE7A   Monday ", "bsse7a monday", "When is Wahab free?"] + [
        f"Query {index}" for index in range(20)
    ]
    assert _normalize_search_history(searches) == [
        "BSSE7A Monday",
        "When is Wahab free?",
        "Query 0",
        "Query 1",
        "Query 2",
        "Query 3",
        "Query 4",
        "Query 5",
    ]


def test_unchanged_search_history_does_not_create_another_firestore_write():
    store = FirestoreStore.__new__(FirestoreStore)
    store.search_history = Mock()
    store._search_history_cache = TTLCache(ttl_seconds=300, max_entries=4)
    store._search_history_cache.set("student", ["BSSE7A Monday"])

    assert store.save_search_history("student", ["  BSSE7A Monday  "]) == ["BSSE7A Monday"]
    store.search_history.document.assert_not_called()

def test_firestore_payload_round_trip_is_compact_and_lossless():
    payload = {
        "for_day": "Entire Week",
        "items": [
            {
                "semester_display": "BS(SE)-7A",
                "course_title": "Software Project Management",
                "faculty": "Muhammad Qasim",
            }
            for _ in range(500)
        ],
    }

    compressed = _encode_json_payload(payload)

    assert _decode_json_payload(compressed) == payload
    assert len(compressed) < len(str(payload).encode("utf-8")) / 5


def test_cache_hash_ignores_only_volatile_search_timestamp():
    first = {"items": [{"course": "One"}], "search": {"query": "one", "saved_at": "a"}}
    same_content = {"items": [{"course": "One"}], "search": {"query": "one", "saved_at": "b"}}
    different_query = {"items": [{"course": "One"}], "search": {"query": "two", "saved_at": "b"}}

    assert _cache_content_hash(first) == _cache_content_hash(same_content)
    assert _cache_content_hash(first) != _cache_content_hash(different_query)


def test_stale_source_fallback_reuses_the_first_firestore_read():
    source = {"items": [{"course": "Algorithms"}]}
    snapshot = Mock()
    snapshot.exists = True
    snapshot.to_dict.return_value = {
        "source_gzip": _encode_json_payload(source),
        "updated_at": datetime.now(timezone.utc) - timedelta(hours=2),
    }
    document = Mock()
    document.get.return_value = snapshot
    store = FirestoreStore.__new__(FirestoreStore)
    store.source_cache = Mock()
    store.source_cache.document.return_value = document
    store._source_cache = TTLCache(ttl_seconds=1800, max_entries=4)
    store._source_hashes = TTLCache(ttl_seconds=86400, max_entries=4)

    assert store.get_search_source_cache("student", max_age_seconds=1800) is None
    assert store.get_search_source_cache("student", max_age_seconds=None) == source
    document.get.assert_called_once()


def test_weekly_snapshot_is_saved_without_a_firestore_expiry():
    store = FirestoreStore.__new__(FirestoreStore)
    store.source_cache = Mock()
    store._source_cache = TTLCache(ttl_seconds=1800, max_entries=4)
    store._source_hashes = TTLCache(ttl_seconds=1800, max_entries=4)

    assert store.save_search_source_cache("student", {"snapshot_version": 1, "items": []})
    payload = store.source_cache.document.return_value.set.call_args.args[0]
    assert "expires_at" not in payload
