"""
plugins/news_plugin.py — Real-time news plugin for JARVIS.

Fetches current cybersecurity, AI, and software engineering news from
authoritative RSS feeds. Always uses the local system date and returns
dated items with source links.

No API key required.
"""

from datetime import datetime, timedelta, timezone
import re
import xml.etree.ElementTree as ET
from typing import Optional

import requests


PLUGIN_INFO = {
    "name": "news",
    "description": (
        "Fetches current, real-time news from authoritative cybersecurity, AI, "
        "and software engineering RSS feeds. Always uses the current system date. "
        "Use this whenever the user asks for latest news, today's news, current events, "
        "or up-to-date cybersecurity/AI/software developments. "
        "Do not use web_search for news; use news instead."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "description": "news category: cybersecurity | ai | software | all (default: all)"
            },
            "limit": {
                "type": "INTEGER",
                "description": "maximum number of news items to return (default: 10, max: 20)"
            }
        },
        "required": []
    }
}


RSS_FEEDS = {
    "cybersecurity": [
        "https://feeds.feedburner.com/TheHackersNews",
        "https://www.bleepingcomputer.com/feed/",
        "https://krebsonsecurity.com/feed/",
        "https://www.darkreading.com/rss.xml",
    ],
    "ai": [
        "https://www.technologyreview.com/topic/artificial-intelligence/feed",
        "https://venturebeat.com/category/ai/feed/",
        "https://blog.google/technology/ai/rss/",
    ],
    "software": [
        "https://github.blog/feed/",
        "https://devblogs.microsoft.com/feed/",
        "https://www.infoq.com/feed/",
    ],
}


def _parse_rss_date(date_str: str) -> Optional[datetime]:
    """Convert RSS pubDate to timezone-aware UTC datetime."""
    if not date_str:
        return None

    cleaned = re.sub(r"^[A-Za-z]{3},\s*", "", date_str.strip())
    cleaned = re.sub(r"\s*GMT$", "", cleaned)

    try:
        dt = datetime.strptime(cleaned, "%d %b %Y %H:%M:%S %z")
        return dt.astimezone(timezone.utc)
    except Exception:
        try:
            dt = datetime.strptime(cleaned, "%Y-%m-%dT%H:%M:%SZ")
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            return None


def _fetch_rss(url: str, timeout: int = 15) -> list[dict]:
    """Fetch and parse a single RSS feed."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.content)
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            pub_date = item.findtext("pubDate", "").strip()
            description = item.findtext("description", "").strip()
            description = re.sub(r"<[^>]+>", "", description)[:300]

            dt = _parse_rss_date(pub_date)
            items.append({
                "title": title,
                "link": link,
                "published": dt,
                "source": url.split("/")[2],
                "description": description,
            })
        return items
    except Exception:
        return []


def get_latest_news(category: str = "all", max_age_days: int = 7, limit_per_feed: int = 5) -> list[dict]:
    """
    Fetch and merge latest news items from RSS feeds.

    category: "all", "cybersecurity", "ai", "software"
    max_age_days: exclude items older than this
    limit_per_feed: max items per feed after filtering
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    if category == "all":
        feeds = []
        for feeds_list in RSS_FEEDS.values():
            feeds.extend(feeds_list)
    else:
        feeds = RSS_FEEDS.get(category, [])

    all_items = []
    for url in feeds:
        for item in _fetch_rss(url):
            if item["published"] and item["published"] >= cutoff:
                all_items.append(item)

    all_items.sort(key=lambda x: x["published"], reverse=True)

    seen_sources = {}
    limited = []
    for item in all_items:
        src = item["source"]
        seen_sources.setdefault(src, 0)
        if seen_sources[src] < limit_per_feed:
            limited.append(item)
            seen_sources[src] += 1

    return limited


def execute(parameters: dict, player=None, speak=None) -> str:
    """
    Plugin entry point.

    parameters:
        category: "cybersecurity", "ai", "software", or "all"
        limit: max number of items to return
    """
    category = (parameters or {}).get("category", "all").lower().strip()
    limit = int((parameters or {}).get("limit", 10))

    items = get_latest_news(category=category, max_age_days=7)

    if not items:
        return "No fresh news found from the configured feeds, sir."

    lines = []
    today = datetime.now().strftime("%A, %B %d, %Y")
    lines.append(f"Latest {category} news as of {today}:\n")

    for idx, item in enumerate(items[:limit], 1):
        pub = item["published"].strftime("%Y-%m-%d %H:%M UTC") if item["published"] else "unknown date"
        lines.append(f"{idx}. {item['title']}")
        lines.append(f"   Source: {item['source']} | {pub}")
        if item["description"]:
            lines.append(f"   {item['description']}")
        lines.append(f"   Link: {item['link']}\n")

    return "\n".join(lines)