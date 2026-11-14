"""Free context for the AI analyst: Google News RSS headlines + known market-moving dates."""
from __future__ import annotations

import calendar
import datetime as dt
import xml.etree.ElementTree as ET

from zari.config import settings
from zari.web import get

RSS = "https://news.google.com/rss/search"
QUERIES = [
    "gold price",
    "Federal Reserve interest rates",
    "US dollar index",
    "US inflation CPI OR jobs report",
    "Iran OR Middle East oil prices",
    "RBI rupee OR India gold import duty",
]


def parse_rss(xml: str, limit: int) -> list[dict]:
    items = []
    for it in ET.fromstring(xml).iter("item"):
        title = (it.findtext("title") or "").strip()
        if title:
            items.append({"title": title, "date": (it.findtext("pubDate") or "")[:16],
                          "source": (it.findtext("source") or "").strip()})
        if len(items) >= limit:
            break
    return items


def headlines(per_query: int = 3) -> tuple[list[dict], list[str]]:
    """About 15-18 fresh headlines (last 2 days). Returns (items, errors)."""
    out, errors, seen = [], [], set()
    for q in QUERIES:
        try:
            xml = get(RSS, params={"q": f"{q} when:2d", "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
            for h in parse_rss(xml, per_query):
                if h["title"] not in seen:
                    seen.add(h["title"])
                    out.append(h)
        except Exception as e:
            errors.append(f"{q}: {e}")
    return out, errors


def first_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def events_in_window(year: int, month: int) -> list[str]:
    """Market-moving dates inside the 1st-deadline window.
    US jobs report: usually the first Friday (approximate). Other dates (Fed meetings, US CPI,
    Indian budget) come from config.yaml `events:` - add them as they are announced."""
    deadline = settings()["plan"]["deadline_day"]
    out = []
    ff = first_friday(year, month)
    if ff.day <= deadline:
        out.append(f"{ff:%a %d %b}: US jobs report (usually first Friday) - prices can swing")
    for e in settings().get("events") or []:
        d = dt.date.fromisoformat(str(e["date"]))
        if d.year == year and d.month == month and d.day <= min(deadline, calendar.monthrange(year, month)[1]):
            out.append(f"{d:%a %d %b}: {e['name']}")
    return out
