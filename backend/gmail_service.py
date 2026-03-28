# gmail_service.py — Gmail API integration
import json
import base64
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional, Dict

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import settings

# Gmail OAuth2 scopes required
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Keywords that signal an inquiry email
INQUIRY_KEYWORDS = [
    "price", "pricing", "cost", "quote", "quotation", "how much",
    "available", "availability", "stock", "in stock",
    "details", "more info", "information", "inquiry", "enquiry",
    "interested", "purchase", "buy", "order", "package", "plan",
    "demo", "trial", "schedule", "appointment", "meeting",
]


def build_oauth_flow() -> Flow:
    """Create an OAuth2 flow for Gmail authorization."""
    client_config = {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uris": [settings.GOOGLE_REDIRECT_URI],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    return Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=settings.GOOGLE_REDIRECT_URI,
    )


def get_auth_url() -> str:
    """Generate the Gmail OAuth2 authorization URL."""
    flow = build_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # Force refresh token on every auth
    )
    return auth_url


def exchange_code_for_token(code: str) -> dict:
    """Exchange authorization code for OAuth2 tokens."""
    flow = build_oauth_flow()
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes),
    }


def build_gmail_service(token_json: str):
    """Build an authenticated Gmail API service from stored token JSON."""
    token_data = json.loads(token_json)
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id", settings.GOOGLE_CLIENT_ID),
        client_secret=token_data.get("client_secret", settings.GOOGLE_CLIENT_SECRET),
        scopes=token_data.get("scopes", SCOPES),
    )

    # Auto-refresh expired token
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build("gmail", "v1", credentials=creds)


def decode_email_body(payload: dict) -> str:
    """Recursively extract plain text from Gmail message payload."""
    body = ""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            body = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    elif payload.get("mimeType", "").startswith("multipart/"):
        for part in payload.get("parts", []):
            body = decode_email_body(part)
            if body:
                break
    return body


def is_inquiry_email(subject: str, body: str) -> bool:
    """Return True if the email contains inquiry-related keywords."""
    text = f"{subject} {body}".lower()
    return any(kw in text for kw in INQUIRY_KEYWORDS)


def fetch_recent_emails(service, max_results: int = 20) -> List[Dict]:
    """Fetch recent inbox messages from Gmail."""
    try:
        result = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=max_results,
        ).execute()

        messages = result.get("messages", [])
        email_list = []

        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me",
                id=msg_ref["id"],
                format="full",
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
            body = decode_email_body(msg["payload"])

            email_list.append({
                "message_id": msg["id"],
                "thread_id": msg["threadId"],
                "from_email": extract_email(headers.get("From", "")),
                "from_name": extract_name(headers.get("From", "")),
                "subject": headers.get("Subject", "(no subject)"),
                "body": body[:2000],  # Limit to 2000 chars
                "date": headers.get("Date", ""),
            })

        return email_list
    except HttpError as e:
        print(f"Gmail API error: {e}")
        return []


def get_thread_messages(service, thread_id: str) -> List[Dict]:
    """Get all messages in a Gmail thread (to detect replies)."""
    try:
        thread = service.users().threads().get(
            userId="me",
            id=thread_id,
        ).execute()
        return thread.get("messages", [])
    except HttpError:
        return []


def has_user_replied(service, thread_id: str, user_email: str) -> bool:
    """
    Check if the user (account owner) has replied in a thread.
    Returns True if any message in the thread was SENT by our user.
    """
    messages = get_thread_messages(service, thread_id)
    for msg in messages:
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        from_header = headers.get("From", "")
        # If a message was sent FROM our user, they replied
        if user_email.lower() in from_header.lower():
            return True
    return False


def send_reply_in_thread(
    service,
    thread_id: str,
    to_email: str,
    subject: str,
    body: str,
    original_message_id: str,
) -> Optional[str]:
    """
    Send an email reply within the same Gmail thread.
    Returns the sent message ID or None on failure.
    """
    try:
        # Build MIME message
        message = MIMEMultipart("alternative")
        message["To"] = to_email
        message["Subject"] = f"Re: {subject}" if not subject.startswith("Re:") else subject
        message["In-Reply-To"] = original_message_id
        message["References"] = original_message_id

        # Plain text part
        text_part = MIMEText(body, "plain")
        message.attach(text_part)

        # Encode and send
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()

        return sent.get("id")
    except HttpError as e:
        print(f"Failed to send email: {e}")
        return None


def extract_email(from_header: str) -> str:
    """Extract email address from 'Name <email@example.com>' format."""
    match = re.search(r"<(.+?)>", from_header)
    if match:
        return match.group(1).strip()
    return from_header.strip()


def extract_name(from_header: str) -> str:
    """Extract display name from 'Name <email@example.com>' format."""
    match = re.search(r"^(.+?)\s*<", from_header)
    if match:
        return match.group(1).strip().strip('"')
    return ""
