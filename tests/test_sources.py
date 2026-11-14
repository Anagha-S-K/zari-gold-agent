"""Parser tests on saved pages (fixtures captured 6 Nov 2026)."""
import datetime as dt
from pathlib import Path

from zari.sources import goodreturns, grt, ibja, mjdta

F = Path(__file__).parent / "fixtures"


def page(name: str) -> str:
    return (F / name).read_text(encoding="utf-8", errors="ignore")


def test_grt_reads_22kt_rate():
    assert grt.parse(page("grt.html")).rate_22k == 13150.0


def test_grt_falls_back_to_button_text():
    html = '<button class="dropdown-toggle">GOLD 22 KT/1g -  ₹ 13,385</button>'
    assert grt.parse(html).rate_22k == 13385.0


def test_mjdta_home_rate_and_update_time():
    r = mjdta.parse_home(page("mjdta_home.html"))
    assert (r.rate_22k, r.date, r.time, r.note) == (13140.0, dt.date(2026, 11, 6), "10:32", "morning")


def test_mjdta_history_rows():
    rows = mjdta.parse_history(page("mjdta_history.html"))
    assert len(rows) == 9                                   # 1-10 Oct 2026, no Sunday 4 Oct
    assert (rows[0].date, rows[0].time, rows[0].rate_22k) == (dt.date(2026, 10, 1), "09:53", 13680.0)
    assert rows[-1].date == dt.date(2026, 10, 10)


def test_goodreturns_rate_and_date():
    r = goodreturns.parse(page("goodreturns.html"))
    assert (r.rate_22k, r.date) == (13150.0, dt.date(2026, 11, 6))


def test_ibja_latest_916_per_gram():
    r = ibja.parse(page("ibja.html"))
    assert r.date == dt.date(2026, 11, 5) and r.time == "PM"
    assert 10000 < r.rate_22k < 20000                       # per gram, not per 10 g
