"""IBJA - national 916 (22K) benchmark, AM and PM. A signal, not the pay rate."""
from __future__ import annotations

import datetime as dt
import re

from zari.models import RateRecord
from zari.web import get

URL = "https://ibjarates.com/"
# Rows in the "previous dates" table: <td data-label="AM"> 05/11/2026 ... <td data-label="Gold 916">131858
_ROW = re.compile(
    r'data-label="(AM|PM)"\s*>\s*(?:<strong>\s*)?(\d{2}/\d{2}/\d{4})(?:(?!data-label="(?:AM|PM)").)*?'
    r'data-label="Gold 916"\s*>\s*([\d.]+)', re.S)


def parse_all(html: str) -> list[RateRecord]:
    recs = []
    for session, d, per10 in _ROW.findall(html):
        date = dt.datetime.strptime(d, "%d/%m/%Y").date()
        recs.append(RateRecord("ibja", date, round(float(per10) / 10, 2), session, "916 per gram"))
    return recs


def parse(html: str) -> RateRecord:
    recs = parse_all(html)
    if not recs:
        raise ValueError("IBJA: no 916 rates found")
    return max(recs, key=lambda r: (r.date, r.time == "PM"))


def fetch() -> RateRecord:
    return parse(get(URL))
