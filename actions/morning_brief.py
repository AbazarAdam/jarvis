"""
morning_brief.py — JARVIS Morning Brief Agent
Fetches cybersecurity news, AI news, and unread emails, then speaks a summary.
"""

import os
import pickle
import json
import time
import subprocess
from pathlib import Path
from datetime import datetime
import threading

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# Gmail API scope (read‑only)
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

def _get_gmail_service():
    """Authenticate and return a Gmail API service instance."""
    creds = None
    token_path = Path(__file__).resolve().parent.parent / "config" / "gmail_token.pickle"
    creds_path = Path(__file__).resolve().parent.parent / "config" / "gmail_credentials.json"

    if token_path.exists():
        with open(token_path, 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'wb') as token:
            pickle.dump(creds, token)

    return build('gmail', 'v1', credentials=creds)


def _fetch_unread_emails(max_results=5):
    """Return a list of (sender, subject) for unread primary emails."""
    try:
        service = _get_gmail_service()
        results = service.users().messages().list(
            userId='me', maxResults=max_results, q='is:unread category:primary'
        ).execute()
        messages = results.get('messages', [])
        if not messages:
            return []

        emails = []
        for msg in messages[:max_results]:
            msg_data = service.users().messages().get(
                userId='me', id=msg['id'], format='metadata',
                metadataHeaders=['From', 'Subject']
            ).execute()
            headers = msg_data['payload']['headers']
            sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
            subject = next((h['value'] for h in headers if h['name'] == 'Subject'), '(no subject)')
            emails.append((sender, subject))
        return emails
    except Exception as e:
        print(f"[Brief] ⚠️ Email fetch failed: {e}")
        return []


def _get_latest_news_items(category: str, limit: int = 3):
    """Fetch latest news items from the real-time RSS news plugin."""
    try:
        from plugins.news_plugin import get_latest_news
        items = get_latest_news(category=category, max_age_days=7)
        return items[:limit]
    except Exception as e:
        print(f"[Brief] ⚠️ News plugin unavailable: {e}")
        return []


def _format_news_item(item: dict) -> str:
    pub = item.get("published")
    if pub:
        pub_str = pub.strftime("%Y-%m-%d %H:%M UTC")
    else:
        pub_str = "unknown date"
    return f"{item.get('title','')} ({item.get('source','')}, {pub_str})"


def _speak_report(text: str, speak_callback=None):
    """Send the report to Jarvis's voice output."""
    if speak_callback:
        speak_callback(text)
    else:
        print(f"[Brief] 🗣️ {text}")


def morning_brief(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
    speak=None
) -> str:
    """
    Generate and speak the morning brief.
    parameters:
        save   : bool – save report to desktop (default True)
        speak  : bool – speak the report (default True)
    """
    save_to_desktop = parameters.get('save', True) if parameters else True
    speak_aloud = parameters.get('speak', True) if parameters else True

    report_lines = []
    report_lines.append("MORNING BRIEF")
    report_lines.append("=" * 50)

    # 1. Cybersecurity News
    report_lines.append("\n🔐 CYBERSECURITY HEADLINES")
    cyber_items = _get_latest_news_items("cybersecurity", 3)
    if cyber_items:
        for i, item in enumerate(cyber_items, 1):
            report_lines.append(f"  {i}. {_format_news_item(item)}")
    else:
        report_lines.append("  No current cybersecurity news found.")

    # 2. AI & Software Engineering News
    report_lines.append("\n🤖 AI & SOFTWARE ENGINEERING NEWS")
    ai_items = _get_latest_news_items("ai", 2)
    sw_items = _get_latest_news_items("software", 2)
    combined_ai_items = (ai_items + sw_items)[:3]
    if combined_ai_items:
        for i, item in enumerate(combined_ai_items, 1):
            report_lines.append(f"  {i}. {_format_news_item(item)}")
    else:
        report_lines.append("  No current AI/software news found.")

    # 3. Unread Emails
    report_lines.append("\n📧 UNREAD EMAILS")
    emails = _fetch_unread_emails(5)
    if emails:
        for i, (sender, subject) in enumerate(emails, 1):
            short_sender = sender.split('<')[0].strip().strip('"')
            report_lines.append(f"  {i}. {short_sender}: {subject}")
    else:
        report_lines.append("  No unread emails in primary inbox.")

    report_lines.append("\n" + "=" * 50)
    timestamp = datetime.now().strftime("%A, %B %d, %Y — %I:%M %p")
    report_lines.append(f"Report generated: {timestamp}")

    full_report = "\n".join(report_lines)

    # Save to desktop
    if save_to_desktop:
        try:
            desktop = Path.home() / "Desktop"
            filepath = desktop / f"morning_brief_{datetime.now().strftime('%Y-%m-%d')}.txt"
            filepath.write_text(full_report, encoding="utf-8")
            full_report += f"\n\n📄 Full report saved to {filepath}"
        except Exception as e:
            full_report += f"\n\n⚠️ Could not save report: {e}"

    # Always speak a condensed version
    if speak:
        # Build a concise spoken brief — no links, just headlines
        cyber_spoken = cyber_items[0]["title"][:120] if cyber_items else "no cybersecurity updates"
        ai_spoken = combined_ai_items[0]["title"][:120] if combined_ai_items else "no AI news"
        spoken = (
            f"Good morning, sir. "
            f"In cybersecurity: {cyber_spoken}. "
            f"In AI and software: {ai_spoken}. "
            f"You have {len(emails)} unread emails. "
            f"The full report is on your desktop."
        )
        speak(spoken)

    return full_report