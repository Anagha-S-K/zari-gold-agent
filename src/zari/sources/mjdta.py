"""Madras Jewellers & Diamond Traders Association - board rate GRT follows, plus history since 2006."""
from __future__ import annotations

import datetime as dt
import re

from zari.models import RateRecord
from zari.web import get

HOME = "https://thejewellersassociation.org/"
HISTORY = "https://www.thejewellersassociation.org/searchresult.php"

_RATE = re.compile(r"""\$\(['"]#goldrate_22ct['"]\)\.html\(["']([\d.]+)["']\)""")
_UPDATED = re.compile(r"""#updatetime['"]\)\.html\(["'](\d{2}/\d{2}/\d{4})\s+(Morning|Evening|Afternoon)?\s*(\d{1,2}:\d{2}\s*[AP]M)""", re.I)
_ROW = re.compile(
    r"<tr>\s*<td[^>]*>\s*\d+\s*</td>\s*<td[^>]*>\s*(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}\s*[ap]m)\s*</td>\s*"
    r"<td[^>]*>\s*([\d.]+)\s*</td>", re.I)


def _hhmm(t: str) -> str:
    return dt.datetime.strptime(t.strip().upper().replace(" ", ""), "%I:%M%p").strftime("%H:%M")


def parse_home(html: str) -> RateRecord:
    m = _RATE.search(html)
    if not m:
        raise ValueError("MJDTA: 22K rate not found on home page")
    u = _UPDATED.search(html)
    date = dt.datetime.strptime(u.group(1), "%d/%m/%Y").date() if u else None
    time = _hhmm(u.group(3)) if u else ""
    note = (u.group(2) or "").lower() if u else "no update time"
    return RateRecord("mjdta", date, float(m.group(1)), time, note)


def fetch() -> RateRecord:
    return parse_home(get(HOME))


def parse_history(html: str) -> list[RateRecord]:
    out = []
    for d, t, rate in _ROW.findall(html):
        out.append(RateRecord("mjdta", dt.datetime.strptime(d, "%d/%m/%Y").date(), float(rate), _hhmm(t)))
    return out


def history_params(start: dt.date, end: dt.date, page: int = 1) -> dict:
    return {
        "type": "ratesearch",
        "from_date": start.strftime("%d/%m/%Y"),
        "to_date": end.strftime("%d/%m/%Y"),
        "commodity": 1,       # gold
        "rate_timing": 3,     # morning + evening
        "page": page,
    }


def fetch_history_page(start: dt.date, end: dt.date, page: int = 1) -> str:
    return get(HISTORY, params=history_params(start, end, page))
