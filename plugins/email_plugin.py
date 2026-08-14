"""
plugins/email_plugin.py — Gmail plugin for JARVIS.

Actions:
  send   → send an email
  read   → read emails using a Gmail search query (default: is:unread)
  reply  → reply to a specific message by ID
  search → search emails and return summaries
"""

import base64
import pickle
from pathlib import Path
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


PLUGIN_INFO = {
    "name": "email_plugin",
    "description": (
        "Send, read, search, and reply to Gmail emails. "
        "Use for 'send an email to John', 'read my unread emails', "
        "'reply to the latest email from mom', etc."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "send | read | reply | search"
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address for send"
            },
            "subject": {
                "type": "STRING",
                "description": "Email subject"
            },
            "body": {
                "type": "STRING",
                "description": "Email body text"
            },
            "query": {
                "type": "STRING",
                "description": "Gmail search query (e.g., 'from:john@gmail.com is:unread')"
            },
            "max_results": {
                "type": "INTEGER",
                "description": "Number of emails to read (default 5)"
            },
            "reply_body": {
                "type": "STRING",
                "description": "Reply body text"
            },
            "message_id": {
                "type": "STRING",
                "description": "ID of the message to reply to (optional; if omitted, Jarvis may search for the latest matching message)"
            }
        },
        "required": ["action"]
    }
}


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
CREDENTIALS_FILE = CONFIG_DIR / "gmail_credentials.json"
TOKEN_FILE = CONFIG_DIR / "gmail_token_email.pickle"


def _get_service():
    creds = None
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(creds, token)

    return build("gmail", "v1", credentials=creds)


def _create_message(sender, to, subject, body):
    msg = MIMEText(body)
    msg["to"] = to
    msg["from"] = sender
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return {"raw": raw}


def _send_email(to, subject, body):
    if not to or not subject or not body:
        return "Please provide 'to', 'subject', and 'body'."

    service = _get_service()
    profile = service.users().getProfile(userId="me").execute()
    sender = profile["emailAddress"]

    message = _create_message(sender, to, subject, body)
    service.users().messages().send(userId="me", body=message).execute()
    return f"Email sent to {to} with subject '{subject}'."


def _read_emails(query, max_results=5):
    service = _get_service()
    params = {"userId": "me", "maxResults": max_results, "q": query}
    result = service.users().messages().list(**params).execute()
    messages = result.get("messages", [])

    if not messages:
        return "No emails found."

    summaries = []
    for i, m in enumerate(messages[:max_results], 1):
        msg = service.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = msg.get("payload", {}).get("headers", [])
        from_h = next((h["value"] for h in headers if h["name"] == "From"), "Unknown")
        subj = next((h["value"] for h in headers if h["name"] == "Subject"), "(no subject)")
        date = next((h["value"] for h in headers if h["name"] == "Date"), "")
        snippet = msg.get("snippet", "")[:100]
        summaries.append(
            f"{i}. From: {from_h}\n   Subject: {subj}\n   Date: {date}\n   Preview: {snippet}"
        )

    return "Emails:\n" + "\n\n".join(summaries)


def _reply_to_message(message_id, body):
    if not message_id:
        return "Please provide the message_id of the email to reply to."
    if not body:
        return "Please provide the reply_body."

    service = _get_service()
    original = service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["From", "Subject", "Message-ID"],
    ).execute()
    headers = original.get("payload", {}).get("headers", [])
    from_h = next((h["value"] for h in headers if h["name"] == "From"), "")
    subj = next((h["value"] for h in headers if h["name"] == "Subject"), "")
    msg_id = next((h["value"] for h in headers if h["name"] == "Message-ID"), "")

    reply = MIMEText(body)
    reply["to"] = from_h
    reply["subject"] = subj if subj.startswith("Re:") else "Re: " + subj
    reply["In-Reply-To"] = msg_id
    reply["References"] = msg_id

    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    service.users().messages().send(
        userId="me",
        body={
            "raw": raw,
            "threadId": original.get("threadId"),
        },
    ).execute()
    return f"Reply sent to {from_h}."


def execute(parameters: dict, player=None, speak=None) -> str:
    action = (parameters or {}).get("action", "").lower().strip()

    if action == "send":
        return _send_email(
            to=parameters.get("to", ""),
            subject=parameters.get("subject", ""),
            body=parameters.get("body", ""),
        )

    elif action in ("read", "search"):
        query = parameters.get("query", "is:unread")
        max_results = int(parameters.get("max_results", 5))
        return _read_emails(query, max_results)

    elif action == "reply":
        return _reply_to_message(
            message_id=parameters.get("message_id", ""),
            body=parameters.get("reply_body", ""),
        )

    return f"Unknown email action: {action}"