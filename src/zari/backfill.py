"""Load MJDTA daily 22K history (morning + evening rates) into data/rates.csv.

Fetches one month at a time, follows pagination (20 rows/page), pauses between requests,
and skips months already stored, so it can be stopped and resumed."""
from __future__ import annotations

import calendar
import datetime as dt
import re
import time

from zari.config import settings, today_ist
from zari.sources import mjdta
from zari.store import load_rates, save_rates

_PAGES = re.compile(r"Page\s+\d+\s+of\s+(\d+)")


def page_count(html: str) -> int:
    m = _PAGES.search(html)
    return int(m.group(1)) if m else 1


def months(start: str, end: dt.date):
    y, m = map(int, start.split("-"))
    while (y, m) <= (end.year, end.month):
        first = dt.date(y, m, 1)
        last = dt.date(y, m, calendar.monthrange(y, m)[1])
        yield first, min(last, end)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def backfill(start: str | None = None, end: dt.date | None = None, log=print) -> dict:
    cfg = settings()["backfill"]
    start = start or cfg["start"]
    end = end or today_ist()
    have = load_rates()
    have = have[have.source == "mjdta"]
    done_months = {(d.year, d.month) for d in have["date"]}
    current = (end.year, end.month)
    stats = {"months": 0, "skipped": 0, "rows": 0, "errors": []}

    for first, last in months(start, end):
        key = (first.year, first.month)
        if key in done_months and key != current:   # finished month already stored
            stats["skipped"] += 1
            continue
        try:
            html = mjdta.fetch_history_page(first, last, 1)
            recs = mjdta.parse_history(html)
            for page in range(2, page_count(html) + 1):
                time.sleep(cfg["pause_seconds"])
                recs += mjdta.parse_history(mjdta.fetch_history_page(first, last, page))
            added = save_rates(recs)
            stats["months"] += 1
            stats["rows"] += added
            log(f"{first:%Y-%m}: {len(recs)} readings ({added} new)")
        except Exception as e:  # report and continue; rerun picks it up
            stats["errors"].append(f"{first:%Y-%m}: {e}")
            log(f"{first:%Y-%m}: FAILED - {e}")
        time.sleep(cfg["pause_seconds"])
    return stats
