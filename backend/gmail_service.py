# gmail_service.py — Gmail API integration.
# All functions gracefully return empty/None if Gmail is not configured.

import json
import base64
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict

from config import settings

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

INQUIRY_KEYWORDS = [
    "price", "pricing", "cost", "quote", "quotation", "how much",
    "available", "availability", "details", "more info", "information",
    "inquiry", "enquiry", "interested", "purchase", "buy", "order",
    "package", "plan", "demo", "trial", "schedule", "appointment",
]


def _require_gmail():
    """Raise a clear error if Gmail credentials are not configured."""
    if not settings.gmail_configured:
        raise RuntimeError(
            "Gmail is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in your environment variables."
        )


def _build_flow():
    from google_auth_oauthlib.flow import Flow
    _require_gmail()
    client_config = {
        "web": {
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
        }
    }
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        client_config, scopes=SCOPES, redirect_uri=settings.GOOGLE_REDIRECT_URI
    )


def get_auth_url() -> str:
    flow = _build_flow()
    url, _ = flow.authorization_url(access_type="offline", prompt="consent")
    return url


def exchange_code_for_token(code: str) -> dict:
    flow = _build_flow()
    flow.fetch_token(code=code)
    c = flow.credentials
    return {
        "token":         c.token,
        "refresh_token": c.refresh_token,
        "token_uri":     c.token_uri,
        "client_id":     c.client_id,
        "client_secret": c.client_secret,
        "scopes":        list(c.scopes),
    }


def build_gmail_service(token_json: str):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    d = json.loads(token_json)
    creds = Credentials(
        token=d.get("token"),
        refresh_token=d.get("refresh_token"),
        token_uri=d.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=d.get("client_id", settings.GOOGLE_CLIENT_ID),
        client_secret=d.get("client_secret", settings.GOOGLE_CLIENT_SECRET),
        scopes=d.get("scopes", SCOPES),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _decode_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    for part in payload.get("parts", []):
        result = _decode_body(part)
        if result:
            return result
    return ""


def is_inquiry_email(subject: str, body: str) -> bool:
    text = f"{subject} {body}".lower()
    return any(kw in text for kw in INQUIRY_KEYWORDS)


def fetch_recent_emails(service, max_results: int = 20) -> List[Dict]:
    try:
        from googleapiclient.errors import HttpError
        result = service.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=max_results
        ).execute()
        emails = []
        for ref in result.get("messages", []):
            msg     = service.users().messages().get(userId="me", id=ref["id"], format="full").execute()
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            body    = _decode_body(msg["payload"])
            emails.append({
                "message_id": msg["id"],
                "thread_id":  msg["threadId"],
                "from_email": _extract_email(headers.get("From", "")),
                "from_name":  _extract_name(headers.get("From", "")),
                "subject":    headers.get("Subject", "(no subject)"),
                "body":       body[:2000],
            })
        return emails
    except Exception as e:
        print(f"[Gmail] fetch error: {e}")
        return []


def has_user_replied(service, thread_id: str, user_email: str) -> bool:
    try:
        thread = service.users().threads().get(userId="me", id=thread_id).execute()
        for msg in thread.get("messages", []):
            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            if user_email.lower() in headers.get("From", "").lower():
                return True
    except Exception as e:
        print(f"[Gmail] thread check error: {e}")
    return False


def send_reply_in_thread(
    service, thread_id: str, to_email: str,
    subject: str, body: str, original_message_id: str,
) -> Optional[str]:
    try:
        msg = MIMEMultipart("alternative")
        msg["To"]          = to_email
        msg["Subject"]     = f"Re: {subject}" if not subject.startswith("Re:") else subject
        msg["In-Reply-To"] = original_message_id
        msg["References"]  = original_message_id
        msg.attach(MIMEText(body, "plain"))
        raw  = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": thread_id}
        ).execute()
        return sent.get("id")
    except Exception as e:
        print(f"[Gmail] send error: {e}")
        return None


def _extract_email(from_header: str) -> str:
    match = re.search(r"<(.+?)>", from_header)
    return match.group(1).strip() if match else from_header.strip()

def _extract_name(from_header: str) -> str:
    match = re.search(r"^(.+?)\s*<", from_header)
    return match.group(1).strip().strip('"') if match else ""
