"""Fetch today's 22K rate from every source, validate it, and pick the authoritative one."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from zari.config import settings, today_ist
from zari.models import RateRecord
from zari.sources import goodreturns, grt, ibja, mjdta

# (name, fetch function, must the source's date equal today?)
SOURCES = [
    ("grt", grt.fetch, False),          # GRT shows no date
    ("mjdta", mjdta.fetch, True),
    ("goodreturns", goodreturns.fetch, True),
    ("ibja", ibja.fetch, False),        # signal only; often yesterday's PM before noon
]


@dataclass
class FetchReport:
    date: dt.date
    records: dict[str, RateRecord] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    pay_rate: float | None = None
    pay_source: str | None = None


def check(rec: RateRecord, today: dt.date, need_today: bool) -> str | None:
    c = settings()["checks"]
    if not (c["min_rate"] <= rec.rate_22k <= c["max_rate"]):
        return f"rate {rec.rate_22k} outside sane range"
    if need_today and rec.date != today:
        return f"stale: page is for {rec.date}, not {today}"
    return None


def fetch_all(today: dt.date | None = None, sources=SOURCES) -> FetchReport:
    today = today or today_ist()
    rep = FetchReport(today)
    for name, fn, need_today in sources:
        try:
            rec = fn()
        except Exception as e:  # never silently skip: record the failure
            rep.errors[name] = str(e)
            continue
        problem = check(rec, today, need_today)
        if problem:
            rep.errors[name] = problem
        else:
            rep.records[name] = rec

    g, m = rep.records.get("grt"), rep.records.get("mjdta")
    if g:
        rep.pay_rate, rep.pay_source = g.rate_22k, "grt"
    elif m:
        rep.pay_rate, rep.pay_source = m.rate_22k, "mjdta"
        rep.warnings.append("GRT unavailable - using MJDTA board rate")
    if g and m and abs(g.rate_22k - m.rate_22k) > settings()["checks"]["grt_mjdta_flag_diff"]:
        rep.warnings.append(f"GRT ₹{g.rate_22k:,.0f} differs from MJDTA ₹{m.rate_22k:,.0f} - trusting GRT")
    if rep.pay_rate is None:
        rep.warnings.append("No pay rate today: GRT and MJDTA both failed")
    return rep
