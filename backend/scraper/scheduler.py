import logging
import os
import sys
import re
import time
import hashlib
import json
from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

# Add parent directory to path for absolute imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.append(parent_dir)

from dateutil import tz
from google.auth.transport.requests import Request as GoogleAuthRequest

from core.ttl_cache import TTLCache
from core.telemetry import increment, observe
from core.resource_limits import positive_int_env

from .gmail_client import (
    build_service,
    get_credentials,
    get_message_html,
    get_message_html_from_message,
    get_message,
    list_messages,
)
from .timetable_parser import (
    TIMETABLE_PARSER_VERSION,
    parse_timetable_html,
    parse_html_with_diagnostics,
)

LOGGER = logging.getLogger(__name__)

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_GMAIL_IDENTITY_CACHE = TTLCache[str, str](
    ttl_seconds=3600,
    max_entries=positive_int_env("CLASSWIRE_IDENTITY_CACHE_ENTRIES", 128, maximum=512),
)
PUBLIC_ITEM_FIELDS = (
    "row_number",
    "semester",
    "semester_key",
    "semester_display",
    "section",
    "class_section",
    "course",
    "course_title",
    "course_code",
    "course_type",
    "faculty",
    "room",
    "time",
    "campus",
    "schedule_day",
)

def _compact_items(items):
    """Strip parser-only diagnostics before network and Firestore persistence."""
    return [
        {
            field: item[field]
            for field in PUBLIC_ITEM_FIELDS
            if item.get(field) not in (None, "", [])
        }
        for item in items
    ]


def _summarize(items):
    semester_counts = {}
    for item in items:
        semester = item.get("semester_display") or item.get("semester") or "Unknown"
        semester_counts[semester] = semester_counts.get(semester, 0) + 1
    return {
        "total_items": len(items),
        "semester_breakdown": semester_counts,
        "unique_courses": len(
            {item.get("course_code") or item.get("course") for item in items if item.get("course_code") or item.get("course")}
        ),
        "unique_faculty": len({item.get("faculty") for item in items if item.get("faculty")}),
    }


def _items_by_weekday(source: Optional[Dict]) -> Dict[str, list]:
    grouped = {day: [] for day in WEEKDAY_NAMES[:6]}
    for item in (source or {}).get("items", []):
        day = item.get("schedule_day")
        if day in grouped:
            grouped[day].append(dict(item))
    return grouped


def _day_items_signature(items: list) -> str:
    """Return an order-independent signature for one parsed weekday."""
    normalized = sorted(
        json.dumps(item, sort_keys=True, default=str, separators=(",", ":"))
        for item in items
    )
    return hashlib.sha256("\n".join(normalized).encode("utf-8")).hexdigest()


def _message_received_at(message: Optional[Dict]) -> Optional[str]:
    value = (message or {}).get("internalDate")
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=tz.UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OSError):
        return None

def _target_day_name(now_local: datetime, next_day_available_hour: int = 17) -> str:
    """
    Determine which day's timetable to look for based on current time.
    
    Logic:
    - If it's after the configured hour (default 5 PM/17:00), look for tomorrow's timetable
    - If it's before that hour, look for today's timetable
    
    This is because the next day's timetable becomes available 
    between 5-11 PM on the previous day.
    
    Args:
        now_local: Current local datetime
        next_day_available_hour: Hour when next day's timetable becomes available (24-hour format)
    """
    if now_local.hour >= next_day_available_hour:
        tomorrow = now_local + timedelta(days=1)
        return WEEKDAY_NAMES[tomorrow.weekday()]
    else:
        return WEEKDAY_NAMES[now_local.weekday()]

def _target_date(now_local: datetime, next_day_available_hour: int = 17) -> datetime:
    """
    Get the target date that corresponds to the day we're looking for.
    
    Args:
        now_local: Current local datetime
        next_day_available_hour: Hour when next day's timetable becomes available (24-hour format)
    """
    if now_local.hour >= next_day_available_hour:
        # Target tomorrow's date
        return now_local + timedelta(days=1)
    else:
        # Target today's date
        return now_local

def _build_query(base: str, day_name: str) -> str:
    # Some schedule emails visually contain "for Tuesday", but Gmail's index
    # splits that phrase across HTML nodes. Match the weekday in the subject as
    # the reliable path and retain the body phrase as a fallback.
    return f'{base} {{subject:{day_name} "for {day_name}"}} -in:trash'


def _build_week_query(base: str) -> str:
    alternatives = " ".join(
        [*(f"subject:{day}" for day in WEEKDAY_NAMES[:6]), *(f'"for {day}"' for day in WEEKDAY_NAMES[:6])]
    )
    return f"{base} {{{alternatives}}} -in:trash"


def _latest_messages_by_weekday(
    service,
    user_email: str,
    gmail_query_base: str,
    day_names: Iterable[str],
) -> Dict[str, Dict]:
    """Find the newest email for each weekday, independently.

    Gmail returns searches newest-first. A newer Monday replaces an older
    Monday while the latest Tuesday remains available, with no age cut-off.
    These requests are deliberately independent so one failed weekday search
    cannot silently disappear inside a batch response.
    """
    latest = {}
    for day_name in day_names:
        messages = list_messages(
            service, user_id=user_email,
            query=_build_query(gmail_query_base, day_name), max_results=1,
        )
        if messages:
            latest[day_name] = messages[0]
    return latest


def _day_from_subject(subject: str) -> Optional[str]:
    lowered = subject.casefold()
    return next((day for day in WEEKDAY_NAMES[:6] if re.search(rf"\b{day.casefold()}\b", lowered)), None)

def _next_date_for_day(now_local: datetime, day_name: str) -> datetime:
    target_weekday = WEEKDAY_NAMES.index(day_name)
    return now_local + timedelta(days=(target_weekday - now_local.weekday()) % 7)

def run_once(user_email: str = "me", user_id: Optional[str] = None, user_settings: Optional[Dict] = None) -> Dict:
    """
    Run the scraper once for a specific user.
    
    Args:
        user_email: Gmail user email (for Gmail API)
        user_id: Firestore user ID for storing results
        user_settings: User-specific settings (overrides global settings)
    """
    from .config import settings
    
    try:
        gmail_query_base = user_settings.get('gmail_query_base', settings.gmail_query_base) if user_settings else settings.gmail_query_base
        timezone = user_settings.get('timezone', settings.tz) if user_settings else settings.tz
        next_day_available_hour = user_settings.get('next_day_available_hour', settings.next_day_available_hour) if user_settings else settings.next_day_available_hour
        timetable_day = user_settings.get('timetable_day', 'Auto') if user_settings else 'Auto'
        should_save_cache = user_settings.get('_save_cache', True) if user_settings else True
        previous_source = user_settings.get('_previous_source') if user_settings else None
        requested_refresh_days = user_settings.get('_refresh_days') if user_settings else None
        
        local_tz = tz.gettz(timezone)
        now_local = datetime.now(tz=local_tz)
        automatic_day = _target_day_name(now_local, next_day_available_hour)
        for_day_name = automatic_day if timetable_day == 'Auto' else timetable_day
        target_date = _target_date(now_local, next_day_available_hour) if timetable_day == 'Auto' else now_local
        if timetable_day not in ('Auto', 'Entire Week'):
            target_date = _next_date_for_day(now_local, timetable_day)

        query = (_build_query(gmail_query_base, for_day_name)
                 if timetable_day != 'Entire Week' else
                 _build_week_query(gmail_query_base))

        LOGGER.info("Looking for: %s  (local: %s)", for_day_name, now_local.strftime("%Y-%m-%d %H:%M"))
        LOGGER.info("Gmail query: %s", query)

        if user_id:
            try:
                from database.firestore_store import data_store
                token_data = data_store.get_user_tokens(user_id)
                if token_data:
                    from google.oauth2.credentials import Credentials
                    
                    expiry = None
                    if token_data.get('expiry'):
                        try:
                            expiry = datetime.fromisoformat(token_data['expiry'].replace('Z', '+00:00'))
                        except Exception:
                            expiry = None
                    
                    creds = Credentials(
                        token=token_data.get('token'),
                        refresh_token=token_data.get('refresh_token'),
                        token_uri=token_data.get('token_uri'),
                        client_id=token_data.get('client_id'),
                        client_secret=token_data.get('client_secret'),
                        scopes=token_data.get('scopes'),
                        expiry=expiry
                    )
                    # google-api-python-client refreshes expired access tokens
                    # in memory, but does not persist the replacement. Without
                    # this, every later scrape refreshes the same stale token.
                    if creds.expired and creds.refresh_token:
                        creds.refresh(GoogleAuthRequest())
                        data_store.save_user_tokens(
                            user_id,
                            {
                                "token": creds.token,
                                "refresh_token": creds.refresh_token,
                                "token_uri": creds.token_uri,
                                "client_id": creds.client_id,
                                "client_secret": creds.client_secret,
                                "scopes": creds.scopes,
                                "expiry": creds.expiry.isoformat() if creds.expiry else None,
                            },
                        )
                else:
                    raise RuntimeError(
                        f"Gmail authorization is missing for {user_email}. "
                        "Sign out, then sign in with that Google account again."
                    )
            except RuntimeError:
                raise
            except Exception as error:
                raise RuntimeError(
                    f"Could not load Gmail authorization for {user_email}. "
                    "Sign out, then reconnect that Google account."
                ) from error
        else:
            creds = get_credentials()

        service = build_service(creds)
        if user_id and user_email and user_email != "me":
            authorized_email = _GMAIL_IDENTITY_CACHE.get(user_id)
            if authorized_email is None:
                profile = service.users().getProfile(userId="me").execute()
                authorized_email = str(profile.get("emailAddress") or "").strip().lower()
                _GMAIL_IDENTITY_CACHE.set(user_id, authorized_email)
            expected_email = str(user_email).strip().lower()
            if authorized_email != expected_email:
                raise RuntimeError(
                    f"Gmail authorization belongs to {authorized_email or 'another account'}, "
                    f"not {expected_email}. Sign out and reconnect {expected_email}."
                )
        if timetable_day == 'Entire Week':
            items = []
            refresh_days = [
                day for day in (requested_refresh_days or WEEKDAY_NAMES[:6])
                if day in WEEKDAY_NAMES[:6]
            ]
            if not refresh_days:
                refresh_days = list(WEEKDAY_NAMES[:6])
            latest_by_day = _latest_messages_by_weekday(
                service,
                user_email,
                gmail_query_base,
                refresh_days,
            )
            previous_ids = (previous_source or {}).get("message_ids_by_day") or {}
            previous_items = _items_by_weekday(previous_source)
            can_reuse_previous = (
                isinstance(previous_source, dict)
                and previous_source.get("snapshot_version") == 1
                and previous_source.get("parser_version") == TIMETABLE_PARSER_VERSION
            )
            changed_days = [
                day for day in WEEKDAY_NAMES[:6]
                if (latest_by_day.get(day) or {}).get("id")
                and (
                    not can_reuse_previous
                    or previous_ids.get(day) != (latest_by_day.get(day) or {}).get("id")
                )
            ]
            candidate_ids = [
                (latest_by_day.get(day) or {}).get("id")
                for day in changed_days
                if (latest_by_day.get(day) or {}).get("id")
            ]
            fetched = {
                message_id: get_message(service, user_email, message_id)
                for message_id in candidate_ids
            }
            increment("gmail.messages_fetched", len(candidate_ids))
            message_ids_by_day = dict(previous_ids)
            received_at_by_day = dict((previous_source or {}).get("source_received_at_by_day") or {})
            refreshed_at_by_day = dict((previous_source or {}).get("refreshed_at_by_day") or {})
            parser_diagnostics = (
                dict((previous_source or {}).get("parser_diagnostics") or {})
                if can_reuse_previous else {}
            )
            content_changed_days = []

            for day_name in WEEKDAY_NAMES[:6]:
                if day_name not in refresh_days:
                    items.extend(previous_items.get(day_name, []))
                    continue
                selected = latest_by_day.get(day_name) or {}
                message_id = selected.get("id")
                if not message_id:
                    items.extend(previous_items.get(day_name, []))
                    # No new email was found. Keep the previous timetable as a
                    # fallback, but do not pretend it was updated today.
                    if received_at_by_day.get(day_name):
                        refreshed_at_by_day[day_name] = received_at_by_day[day_name]
                    else:
                        refreshed_at_by_day.pop(day_name, None)
                    continue
                message_ids_by_day[day_name] = message_id
                if can_reuse_previous and previous_ids.get(day_name) == message_id:
                    day_items = previous_items.get(day_name, [])
                    # Earlier versions stamped the check time even for an old
                    # message. Recover the actual email date where available.
                    if received_at_by_day.get(day_name):
                        refreshed_at_by_day[day_name] = received_at_by_day[day_name]
                    else:
                        refreshed_at_by_day.pop(day_name, None)
                    parser_diagnostics[day_name] = {
                        "parser_version": TIMETABLE_PARSER_VERSION,
                        "accepted_rows": len(day_items),
                        "reused": True,
                    }
                else:
                    message = fetched.get(message_id)
                    if not message:
                        raise RuntimeError(
                            f"The newest {day_name} timetable email could not be loaded. "
                            "Your previously saved timetable has not been replaced."
                        )
                    received_at_by_day[day_name] = _message_received_at(message)
                    if received_at_by_day[day_name]:
                        refreshed_at_by_day[day_name] = received_at_by_day[day_name]
                    else:
                        refreshed_at_by_day.pop(day_name, None)
                    html = get_message_html_from_message(message) or ""
                    parser_started = time.perf_counter()
                    day_items, diagnostics = parse_html_with_diagnostics(html)
                    if not day_items:
                        raise RuntimeError(
                            f"The newest {day_name} timetable email could not be parsed. "
                            "Your previously saved timetable has not been replaced."
                        )
                    observe("parser.timetable", (time.perf_counter() - parser_started) * 1000)
                    increment("parser.rows_accepted", diagnostics.get("accepted_rows", 0))
                    increment(
                        "parser.rows_rejected",
                        diagnostics.get("rejected_missing_identity", 0)
                        + diagnostics.get("rejected_missing_time", 0),
                    )
                    parser_diagnostics[day_name] = {**diagnostics, "reused": False}
                for item in day_items:
                    item["schedule_day"] = day_name
                if _day_items_signature(day_items) != _day_items_signature(previous_items.get(day_name, [])):
                    content_changed_days.append(day_name)
                items.extend(day_items)

            items = _compact_items(items)
            message_ids = [
                message_ids_by_day[day]
                for day in WEEKDAY_NAMES[:6]
                if message_ids_by_day.get(day)
            ]
            doc = {
                "for_day": "Entire Week", "for_date": now_local.date().isoformat(),
                "snapshot_version": 1,
                "query": query, "message_id": message_ids[0] if message_ids else None,
                "message_ids": message_ids, "items": items,
                "message_ids_by_day": message_ids_by_day,
                "source_received_at_by_day": received_at_by_day,
                "refreshed_at_by_day": refreshed_at_by_day,
                "parser_version": TIMETABLE_PARSER_VERSION,
                "parser_diagnostics": parser_diagnostics,
                "refresh": {
                    "changed_days": changed_days,
                    "content_changed_days": content_changed_days,
                    "reused_days": [
                        day for day in refresh_days
                        if message_ids_by_day.get(day) and day not in changed_days
                    ],
                    "requested_days": refresh_days,
                    "fetched_messages": len(candidate_ids),
                },
                "summary": _summarize(items),
            }
            if user_id and should_save_cache:
                from database.firestore_store import data_store
                data_store.save_timetable_cache(user_id, doc)
            return {"success": True, "data": doc, "message": f"Successfully found {len(items)} items for the week"}

        msgs = list_messages(service, user_id=user_email, query=query, max_results=1)
        
        if not msgs:
            LOGGER.warning("No messages found for target date")
            doc = {
                "for_day": for_day_name,
                "for_date": target_date.date().isoformat(),
                "query": query,
                "message_id": None,
                "items": [],
                "summary": {
                    "total_items": 0,
                    "semester_breakdown": {},
                    "unique_courses": 0,
                    "unique_faculty": 0,
                }
            }
            
            # Save to Firestore if user_id provided, otherwise use local storage
            if user_id and should_save_cache:
                from database.firestore_store import data_store
                data_store.save_timetable_cache(user_id, doc)
            return {"success": True, "data": doc, "message": "No messages found for today"}

        msg_id = msgs[0]["id"]
        html = get_message_html(service, user_id=user_email, msg_id=msg_id) or ""
        
        items = _compact_items(parse_timetable_html(html))

        doc = {
            "for_day": for_day_name,
            "for_date": target_date.date().isoformat(),
            "query": query,
            "message_id": msg_id,
            "items": items,
            "summary": _summarize(items),
        }
        
        if user_id and should_save_cache:
            from database.firestore_store import data_store
            data_store.save_timetable_cache(user_id, doc)
        summary = doc.get("summary", {})
        LOGGER.info(f"Successfully parsed {summary['total_items']} items for date {target_date.date()}")
        
        return {"success": True, "data": doc, "message": f"Successfully found {len(items)} items"}
    
    except Exception as e:
        LOGGER.error(f"Scraper error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
