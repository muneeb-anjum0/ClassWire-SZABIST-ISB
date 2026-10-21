import base64

from scraper.gmail_client import get_message_html_from_message, get_message_subject


def test_message_helpers_extract_nested_html_and_subject():
    html = "<table><tr><td>Class</td></tr></table>"
    encoded = base64.urlsafe_b64encode(html.encode()).decode()
    message = {
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [{"name": "Subject", "value": "Class Schedule – Tuesday"}],
            "parts": [{"mimeType": "text/html", "body": {"data": encoded}}],
        }
    }

    assert get_message_subject(message) == "Class Schedule – Tuesday"
    assert get_message_html_from_message(message) == html


def test_message_helper_handles_unpadded_base64_and_unknown_multipart_type():
    html = "<p>Tuesday schedule</p>"
    encoded = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    message = {
        "payload": {
            "mimeType": "multipart/x-custom",
            "parts": [{"mimeType": "text/html", "body": {"data": encoded}}],
        }
    }

    assert get_message_html_from_message(message) == html
