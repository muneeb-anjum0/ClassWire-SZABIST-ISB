"""
Gmail API client + helpers.
"""
import base64
import logging
import os
import random
import time
from typing import Dict, List, Optional

import httplib2
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
]

LOGGER = logging.getLogger(__name__)
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _execute_with_retry(request, *, attempts: int = 3):
    """Execute an API request with bounded exponential backoff and jitter."""
    for attempt in range(attempts):
        try:
            return request.execute()
        except HttpError as error:
            status = getattr(getattr(error, "resp", None), "status", None)
            if status not in RETRYABLE_STATUS_CODES or attempt == attempts - 1:
                raise
            delay = min(2.0, 0.2 * (2 ** attempt)) + random.uniform(0, 0.1)
            LOGGER.warning("Retrying Gmail request after status %s in %.2fs", status, delay)
            time.sleep(delay)

def get_credentials(token_path: str = "token.json", client_secret_path: str = "credentials/client_secret.json") -> Credentials:
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            LOGGER.info("Refreshing Gmail token...")
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_path):
                raise FileNotFoundError(
                    f"Missing OAuth client file at {client_secret_path}. "
                    f"Download from Google Cloud Console → OAuth 2.0 Client IDs."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as token:
            token.write(creds.to_json())
            LOGGER.info("Saved Gmail token to %s", token_path)
    return creds

def build_service(creds: Credentials):
    timeout = max(5, min(int(os.getenv("GMAIL_API_TIMEOUT_SECONDS", "30")), 120))
    authorized_http = AuthorizedHttp(creds, http=httplib2.Http(timeout=timeout))
    return build("gmail", "v1", http=authorized_http, cache_discovery=False)

def list_messages(service, user_id: str, query: str, max_results: int = 10) -> List[Dict]:
    try:
        resp = _execute_with_retry(service.users().messages().list(userId=user_id, q=query, maxResults=max_results))
        return resp.get("messages", []) or []
    except HttpError as e:
        LOGGER.error("Gmail list error: %s", e)
        raise RuntimeError("Gmail search failed; please retry in a moment") from e


def get_message(service, user_id: str, msg_id: str) -> Dict:
    return _execute_with_retry(service.users().messages().get(userId=user_id, id=msg_id, format="full"))


def _decode_body(data: str) -> str:
    padded = data + ("=" * (-len(data) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")

def _walk_parts_for_html(payload) -> Optional[str]:
    if not payload:
        return None

    mime_type = payload.get("mimeType")
    body = payload.get("body", {})

    if mime_type == "text/html":
        data = body.get("data")
        if data:
            return _decode_body(data)

    if payload.get("parts"):
        plain_text_fallback = None
        for part in payload.get("parts", []) or []:
            html = _walk_parts_for_html(part)
            if html:
                if "<" in html and ">" in html:
                    return html
                if plain_text_fallback is None:
                    plain_text_fallback = html

        if plain_text_fallback:
            return plain_text_fallback

    if mime_type == "text/plain":
        data = body.get("data")
        if data:
            return _decode_body(data)

    return None

def get_message_html(service, user_id: str, msg_id: str) -> Optional[str]:
    return get_message_html_from_message(get_message(service, user_id, msg_id))


def get_message_html_from_message(msg: Dict) -> Optional[str]:
    payload = msg.get("payload", {})
    return _walk_parts_for_html(payload)


def get_message_subject(msg: Dict) -> str:
    headers = (msg.get("payload") or {}).get("headers") or []
    for header in headers:
        if str(header.get("name") or "").casefold() == "subject":
            return str(header.get("value") or "")
    return ""
