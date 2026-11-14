"""GRT Jewellers - the authoritative pay rate (header "GOLD 22 KT/1g")."""
from __future__ import annotations

import re

from zari.config import today_ist
from zari.models import RateRecord
from zari.web import get

URL = "https://www.grtjewels.com/"
_JSON = re.compile(r'\?"purity\?"\s*:\s*\?"22 KT\?"\s*,\s*\?"amount\?"\s*:\s*([\d.]+)')
_TEXT = re.compile(r"GOLD 22 KT/1g\s*-\s*(?:₹|&#x20b9;|Rs\.?)\s*([\d,]+(?:\.\d+)?)")


def parse(html: str) -> RateRecord:
    m = _JSON.search(html) or _TEXT.search(html)
    if not m:
        raise ValueError("GRT: 22 KT rate not found on page")
    # GRT shows no date; the reading is for the day it was fetched.
    return RateRecord("grt", today_ist(), float(m.group(1).replace(",", "")))


def fetch() -> RateRecord:
    return parse(get(URL))
