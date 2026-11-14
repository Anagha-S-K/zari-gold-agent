"""GoodReturns Bangalore - cross-check."""
from __future__ import annotations

import datetime as dt
import html as htmllib
import re

from zari.models import RateRecord
from zari.web import get

URL = "https://www.goodreturns.in/gold-rates/bangalore.html"
_RATE = re.compile(r"<strong>\s*(?:₹|&#x20b9;)\s*([\d,]+)\s*</strong>\s*per gram for 22 karat", re.I)
_DATE = re.compile(r"Bangalore Today \((\d{1,2} [A-Za-z]+ \d{4})\)")


def parse(html: str) -> RateRecord:
    m = _RATE.search(html)
    if not m:
        m = _RATE.search(htmllib.unescape(html))
    if not m:
        raise ValueError("GoodReturns: 22K rate not found")
    d = _DATE.search(html)
    date = dt.datetime.strptime(d.group(1), "%d %B %Y").date() if d else None
    return RateRecord("goodreturns", date, float(m.group(1).replace(",", "")))


def fetch() -> RateRecord:
    return parse(get(URL))
