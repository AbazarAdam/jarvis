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


def _search_news(query: str, num_results=3):
    """Use DuckDuckGo to get top news headlines, filtering out AI disclaimers."""
    try:
        from actions.web_search import web_search
        # Use a single, clean query without site: operators (they confuse the LLM)
        result = web_search(parameters={"query": query, "mode": "search"}, player=None)
        lines = result.strip().split('\n')
        headlines = []
        for line in lines:
            clean = line.strip('-• *').strip()
            # Skip lines that are clearly not headlines
            if not clean or len(clean) < 25:
                continue
            if any(phrase in clean.lower() for phrase in [
                'i cannot provide', 'as of my last update', 'i don’t have',
                'i am unable', 'my knowledge is current', 'for the latest',
                'you can visit', 'i recommend', 'here are some',
                'please note', 'note:', 'source:', 'action:'
            ]):
                continue
            # Skip lines that are just URLs
            if clean.startswith('http'):
                continue
            headlines.append(clean)
            if len(headlines) >= num_results:
                break
        return headlines if headlines else ["No specific headlines found."]
    except Exception as e:
        print(f"[Brief] ⚠️ News search failed for '{query}': {e}")
        return [f"Could not fetch news for {query}"]


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
    cyber_news = _search_news("latest cybersecurity news today", 3)
    for i, headline in enumerate(cyber_news, 1):
        report_lines.append(f"  {i}. {headline}")

    # 2. AI & Software Engineering News
    report_lines.append("\n🤖 AI & SOFTWARE ENGINEERING NEWS")
    ai_news = _search_news("latest AI software engineering news today", 3)
    for i, headline in enumerate(ai_news, 1):
        report_lines.append(f"  {i}. {headline}")

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
        cyber_spoken = cyber_news[0][:120] if cyber_news else "no cybersecurity updates"
        ai_spoken = ai_news[0][:120] if ai_news else "no AI news"
        spoken = (
            f"Good morning, sir. "
            f"In cybersecurity: {cyber_spoken}. "
            f"In AI and software: {ai_spoken}. "
            f"You have {len(emails)} unread emails. "
            f"The full report is on your desktop."
        )
        speak(spoken)

    return full_report