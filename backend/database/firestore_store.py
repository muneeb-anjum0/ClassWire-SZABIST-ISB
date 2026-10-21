"""Firestore persistence for users, OAuth tokens, settings, and timetable cache."""

from __future__ import annotations

import gzip
import json
import hashlib
import logging
import os
import uuid
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from core.ttl_cache import TTLCache
from core.telemetry import increment
from core.resource_limits import positive_int_env

from .defaults import build_default_user_settings

logger = logging.getLogger(__name__)
MAX_FIRESTORE_PAYLOAD_BYTES = 900_000
CACHE_RETENTION_DAYS = 7
RECENT_SEARCH_LIMIT = 8


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


def _encode_json_payload(value: Dict[str, Any]) -> bytes:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
    return gzip.compress(serialized, compresslevel=6)


def _decode_json_payload(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, (bytes, bytearray)):
        return None
    try:
        decoded = json.loads(gzip.decompress(bytes(value)).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Ignoring an invalid compressed Firestore payload", exc_info=True)
        return None
    return decoded if isinstance(decoded, dict) else None


def _cache_content_hash(cache_data: Dict[str, Any]) -> str:
    stable_payload = dict(cache_data)
    search = stable_payload.get("search")
    if isinstance(search, dict):
        stable_payload["search"] = {key: value for key, value in search.items() if key != "saved_at"}
    return hashlib.sha256(
        json.dumps(stable_payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _normalize_search_history(searches: Any) -> List[str]:
    if not isinstance(searches, list):
        return []
    result = []
    seen = set()
    for search in searches:
        value = " ".join(str(search or "").split()).strip()
        key = value.casefold()
        if not value or len(value) > 500 or key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= RECENT_SEARCH_LIMIT:
            break
    return result


def _load_firebase_credentials():
    from firebase_admin import credentials

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()

    if service_account_json:
        try:
            return credentials.Certificate(json.loads(service_account_json))
        except json.JSONDecodeError as exc:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT_JSON must contain valid JSON") from exc

    if service_account_path:
        if not os.path.exists(service_account_path):
            raise ValueError(f"FIREBASE_SERVICE_ACCOUNT_PATH does not exist: {service_account_path}")
        return credentials.Certificate(service_account_path)

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return credentials.ApplicationDefault()

    raise ValueError(
        "Firebase credentials are missing. Set FIREBASE_SERVICE_ACCOUNT_JSON, "
        "FIREBASE_SERVICE_ACCOUNT_PATH, or GOOGLE_APPLICATION_CREDENTIALS."
    )


def _initialize_firestore_client():
    import firebase_admin
    from firebase_admin import firestore

    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip() or None

    if firebase_admin._apps:
        return firestore.client()

    cred = _load_firebase_credentials()
    app = firebase_admin.initialize_app(cred, {"projectId": project_id} if project_id else None)
    return firestore.client(app=app)


class FirestoreStore:
    """Small persistence API consumed by the rest of the backend."""

    def __init__(self) -> None:
        self.client = _initialize_firestore_client()
        self.users = self.client.collection("users")
        self.tokens = self.client.collection("gmail_tokens")
        self.settings = self.client.collection("user_settings")
        self.cache = self.client.collection("timetable_cache")
        self.source_cache = self.client.collection("timetable_source_cache")
        self.search_history = self.client.collection("search_history")
        compact_limit = positive_int_env("CLASSWIRE_COMPACT_CACHE_ENTRIES", 128, maximum=512)
        timetable_limit = positive_int_env("CLASSWIRE_TIMETABLE_CACHE_ENTRIES", 24, maximum=128)
        source_limit = positive_int_env("CLASSWIRE_SOURCE_CACHE_ENTRIES", 24, maximum=128)
        self._settings_cache = TTLCache[str, Dict[str, Any]](
            ttl_seconds=300, max_entries=compact_limit
        )
        self._token_cache = TTLCache[str, Dict[str, Any]](
            ttl_seconds=300, max_entries=compact_limit
        )
        self._timetable_cache = TTLCache[str, tuple[Dict[str, Any], str]](
            ttl_seconds=60, max_entries=timetable_limit
        )
        self._timetable_hashes = TTLCache[str, str](
            ttl_seconds=86400, max_entries=compact_limit
        )
        self._source_cache = TTLCache[str, tuple[Dict[str, Any], datetime]](
            ttl_seconds=7 * 24 * 60 * 60, max_entries=source_limit
        )
        self._source_hashes = TTLCache[str, str](
            ttl_seconds=86400, max_entries=compact_limit
        )
        self._search_history_cache = TTLCache[str, List[str]](
            ttl_seconds=300, max_entries=compact_limit
        )
        self._health_cache = TTLCache[str, bool](ttl_seconds=60, max_entries=1)
        logger.info("Firestore client initialized")

    def is_healthy(self) -> bool:
        cached = self._health_cache.get("firestore")
        if cached is not None:
            return cached
        self.users.limit(1).get()
        self._health_cache.set("firestore", True)
        return True

    def get_or_create_user(self, email: str) -> Dict[str, Any]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        normalized_email = email.strip().lower()
        matches = list(self.users.where(filter=FieldFilter("email", "==", normalized_email)).limit(1).stream())
        increment("firestore.read.users")

        if matches:
            payload = matches[0].to_dict() or {}
            payload["id"] = matches[0].id
            return payload

        now = _utc_now()
        user_id = uuid.uuid4().hex
        payload = {
            "id": user_id,
            "email": normalized_email,
            "created_at": now,
            "updated_at": now,
        }
        self.users.document(user_id).set(payload)
        increment("firestore.write.users")
        return {**payload, "created_at": _serialize_timestamp(now), "updated_at": _serialize_timestamp(now)}

    def save_user_tokens(self, user_id: str, token_data: Dict[str, Any]) -> bool:
        from .token_crypto import encrypt_token_data

        self.tokens.document(user_id).set(
            {
                "user_id": user_id,
                "encrypted_token_data": encrypt_token_data(token_data),
                "updated_at": _utc_now(),
            }
        )
        increment("firestore.write.gmail_tokens")
        self._token_cache.set(user_id, dict(token_data))
        return True

    def get_user_tokens(self, user_id: str) -> Optional[Dict[str, Any]]:
        from .token_crypto import decrypt_token_data

        cached = self._token_cache.get(user_id)
        if cached is not None:
            increment("cache.gmail_tokens.hit")
            return dict(cached)
        snapshot = self.tokens.document(user_id).get()
        increment("firestore.read.gmail_tokens")
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict() or {}
        encrypted = payload.get("encrypted_token_data")
        if encrypted:
            token_data = decrypt_token_data(encrypted)
            self._token_cache.set(user_id, dict(token_data))
            return token_data

        legacy = payload.get("token_data")
        if legacy:
            self.save_user_tokens(user_id, legacy)
            return legacy
        return None

    def save_timetable_cache(self, user_id: str, cache_data: Dict[str, Any]) -> bool:
        content_hash = _cache_content_hash(cache_data)
        if self._timetable_hashes.get(user_id) == content_hash:
            return True
        compressed = _encode_json_payload(cache_data)
        if len(compressed) > MAX_FIRESTORE_PAYLOAD_BYTES:
            logger.error("Timetable cache is too large to persist safely: %s compressed bytes", len(compressed))
            return False
        now = _utc_now()
        self.cache.document(user_id).set(
            {
                "user_id": user_id,
                "cache_gzip": compressed,
                "cache_version": 2,
                "updated_at": now,
                "expires_at": now + timedelta(days=CACHE_RETENTION_DAYS),
            }
        )
        increment("firestore.write.timetable_cache")
        self._timetable_hashes.set(user_id, content_hash)
        self._timetable_cache.set(user_id, (cache_data, _serialize_timestamp(now) or ""))
        return True

    def get_latest_timetable_cache(self, user_id: str) -> Optional[Dict[str, Any]]:
        cached = self._timetable_cache.get(user_id)
        if cached is not None:
            increment("cache.timetable.hit")
            return cached[0]
        snapshot = self.cache.document(user_id).get()
        increment("firestore.read.timetable_cache")
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict() or {}
        cache_data = _decode_json_payload(payload.get("cache_gzip")) or payload.get("cache_data")
        if cache_data:
            self._timetable_hashes.set(user_id, _cache_content_hash(cache_data))
            self._timetable_cache.set(
                user_id,
                (cache_data, _serialize_timestamp(payload.get("updated_at")) or ""),
            )
        return cache_data

    def save_search_source_cache(self, user_id: str, source_data: Dict[str, Any]) -> bool:
        """Persist the compact weekly source so process restarts do not rescrape Gmail."""
        content_hash = _cache_content_hash(source_data)
        if self._source_hashes.get(user_id) == content_hash:
            return True
        compressed = _encode_json_payload(source_data)
        if len(compressed) > MAX_FIRESTORE_PAYLOAD_BYTES:
            logger.error("Search source is too large to persist safely: %s compressed bytes", len(compressed))
            return False
        now = _utc_now()
        self.source_cache.document(user_id).set(
            {
                "user_id": user_id,
                "source_gzip": compressed,
                "cache_version": 2,
                "updated_at": now,
            }
        )
        increment("firestore.write.timetable_source_cache")
        self._source_hashes.set(user_id, content_hash)
        self._source_cache.set(user_id, (source_data, now))
        return True

    def get_search_source_cache(
        self,
        user_id: str,
        *,
        max_age_seconds: Optional[int] = 1800,
    ) -> Optional[Dict[str, Any]]:
        cached = self._source_cache.get(user_id)
        if cached is not None:
            source_data, updated_at = cached
            if max_age_seconds is None or (_utc_now() - updated_at).total_seconds() <= max_age_seconds:
                increment("cache.timetable_source.hit")
                return source_data
        snapshot = self.source_cache.document(user_id).get()
        increment("firestore.read.timetable_source_cache")
        if not snapshot.exists:
            return None
        payload = snapshot.to_dict() or {}
        updated_at = payload.get("updated_at")
        if not isinstance(updated_at, datetime):
            return None
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        source_data = _decode_json_payload(payload.get("source_gzip")) or payload.get("source_data")
        if isinstance(source_data, dict):
            self._source_hashes.set(user_id, _cache_content_hash(source_data))
            self._source_cache.set(user_id, (source_data, updated_at))
        # Other callers can impose an age limit, but normal timetable searches
        # request this persisted snapshot without one.
        if max_age_seconds is not None and (_utc_now() - updated_at).total_seconds() > max_age_seconds:
            return None
        return source_data if isinstance(source_data, dict) else None

    def get_bootstrap_data(self, user_id: str) -> Dict[str, Any]:
        """Read the last timetable, using the in-process cache when available."""
        cached_timetable = self._timetable_cache.get(user_id)
        if cached_timetable is not None:
            return {
                "timetable": cached_timetable[0],
                "last_update": cached_timetable[1] or None,
                "search_history": self.get_search_history(user_id),
            }

        cache_ref = self.cache.document(user_id)
        cache_snapshot = cache_ref.get()
        increment("firestore.read.bootstrap_documents")

        timetable = None
        last_update = None
        if cache_snapshot.exists:
            payload = cache_snapshot.to_dict() or {}
            timetable = _decode_json_payload(payload.get("cache_gzip")) or payload.get("cache_data")
            last_update = _serialize_timestamp(payload.get("updated_at"))
            if isinstance(timetable, dict):
                self._timetable_cache.set(user_id, (timetable, last_update or ""))
                self._timetable_hashes.set(user_id, _cache_content_hash(timetable))
        return {
            "timetable": timetable,
            "last_update": last_update,
            "search_history": self.get_search_history(user_id),
        }

    def get_search_history(self, user_id: str) -> List[str]:
        cached = self._search_history_cache.get(user_id)
        if cached is not None:
            increment("cache.search_history.hit")
            return list(cached)
        snapshot = self.search_history.document(user_id).get()
        increment("firestore.read.search_history")
        searches = _normalize_search_history(
            (snapshot.to_dict() or {}).get("searches") if snapshot.exists else []
        )
        self._search_history_cache.set(user_id, searches)
        return list(searches)

    def save_search_history(self, user_id: str, searches: Any) -> List[str]:
        normalized = _normalize_search_history(searches)
        cached = self._search_history_cache.get(user_id)
        if cached == normalized:
            increment("cache.search_history.unchanged")
            return list(normalized)
        self.search_history.document(user_id).set({
            "user_id": user_id,
            "searches": normalized,
            "updated_at": _utc_now(),
        })
        increment("firestore.write.search_history")
        self._search_history_cache.set(user_id, normalized)
        return list(normalized)

    def delete_user_data(self, user_id: str) -> bool:
        """Permanently delete every document and in-process cache for a user."""
        batch = self.client.batch()
        for collection in (
            self.users,
            self.tokens,
            self.settings,
            self.cache,
            self.source_cache,
            self.search_history,
        ):
            batch.delete(collection.document(user_id))
        batch.commit()
        increment("firestore.delete.account_documents", 6)
        for cache in (
            self._settings_cache,
            self._token_cache,
            self._timetable_cache,
            self._timetable_hashes,
            self._source_cache,
            self._source_hashes,
            self._search_history_cache,
        ):
            cache.pop(user_id)
        return True

    def get_latest_timetable_timestamp(self, user_id: str | None = None) -> Optional[str]:
        if user_id:
            cached = self._timetable_cache.get(user_id)
            if cached is not None:
                return cached[1] or None
            snapshot = self.cache.document(user_id).get()
            if not snapshot.exists:
                return None
            return _serialize_timestamp((snapshot.to_dict() or {}).get("updated_at"))

        from firebase_admin import firestore

        docs = list(self.cache.order_by("updated_at", direction=firestore.Query.DESCENDING).limit(1).stream())
        if not docs:
            return None
        return _serialize_timestamp((docs[0].to_dict() or {}).get("updated_at"))

    def save_user_settings(self, user_id: str, settings: Dict[str, Any]) -> bool:
        merged = build_default_user_settings(settings)
        self.settings.document(user_id).set(
            {
                "user_id": user_id,
                "settings": merged,
                "updated_at": _utc_now(),
            }
        )
        increment("firestore.write.user_settings")
        self._settings_cache.set(user_id, dict(merged))
        return True

    def get_user_settings(self, user_id: str) -> Dict[str, Any]:
        cached = self._settings_cache.get(user_id)
        if cached is not None:
            increment("cache.user_settings.hit")
            return dict(cached)
        snapshot = self.settings.document(user_id).get()
        increment("firestore.read.user_settings")
        if not snapshot.exists:
            settings = build_default_user_settings()
        else:
            payload = snapshot.to_dict() or {}
            settings = build_default_user_settings(payload.get("settings") or {})
        self._settings_cache.set(user_id, dict(settings))
        return settings

    def list_users_with_daily_email(self) -> List[Dict[str, Any]]:
        configured_settings: Dict[str, tuple[Dict[str, Any], str]] = {}

        for settings_doc in self.settings.stream():
            payload = settings_doc.to_dict() or {}
            settings = build_default_user_settings(payload.get("settings") or {})
            personal_email = (settings.get("personal_email") or "").strip()
            if not personal_email or not settings.get("daily_email_enabled"):
                continue

            user_id = payload.get("user_id") or settings_doc.id
            configured_settings[user_id] = (settings, personal_email)

        if not configured_settings:
            return []

        configured_users: List[Dict[str, Any]] = []
        user_refs = [self.users.document(user_id) for user_id in configured_settings]
        found_user_ids = set()
        for user_doc in self.client.get_all(user_refs):
            if not user_doc.exists:
                continue
            user_id = user_doc.id
            found_user_ids.add(user_id)
            settings, personal_email = configured_settings[user_id]
            user_data = user_doc.to_dict() or {}
            user_data["id"] = user_id
            configured_users.append(
                {
                    "user": user_data,
                    "settings": settings,
                    "personal_email": personal_email,
                }
            )

        for missing_user_id in configured_settings.keys() - found_user_ids:
            logger.warning("Daily email configured for missing user_id=%s", missing_user_id)

        return configured_users

class LazyFirestoreStore:
    """Create the Firestore store only when the app actually needs it."""

    def __init__(self) -> None:
        self._store: FirestoreStore | None = None
        self._lock = threading.Lock()

    def _get_store(self) -> FirestoreStore:
        if self._store is None:
            with self._lock:
                if self._store is None:
                    self._store = FirestoreStore()
        return self._store

    def is_healthy(self) -> bool:
        try:
            return self._get_store().is_healthy()
        except Exception:
            return False

    def __getattr__(self, name: str):
        return getattr(self._get_store(), name)


data_store = LazyFirestoreStore()
