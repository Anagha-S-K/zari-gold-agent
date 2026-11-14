"""Checks, storage and decision-rule tests (no network)."""
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from zari import analysis, fetch, store
from zari.models import RateRecord


def test_sanity_check_rejects_bad_and_stale_rates():
    today = dt.date(2026, 11, 6)
    assert fetch.check(RateRecord("x", today, 13150), today, True) is None
    assert "sane" in fetch.check(RateRecord("x", today, 131500), today, True)      # per-10g by mistake
    assert "stale" in fetch.check(RateRecord("x", dt.date(2026, 11, 3), 12800), today, True)


def test_fetch_falls_back_to_mjdta_and_reports_failures():
    def boom():
        raise RuntimeError("down")
    day = dt.date(2026, 11, 6)
    rep = fetch.fetch_all(day, sources=[("grt", boom, False),
                                        ("mjdta", lambda: RateRecord("mjdta", day, 13140), True)])
    assert rep.pay_rate == 13140 and rep.pay_source == "mjdta"
    assert "grt" in rep.errors and any("GRT unavailable" in w for w in rep.warnings)


def test_fetch_flags_grt_mjdta_gap():
    day = dt.date(2026, 11, 6)
    rep = fetch.fetch_all(day, sources=[("grt", lambda: RateRecord("grt", day, 13200), False),
                                        ("mjdta", lambda: RateRecord("mjdta", day, 13140), True)])
    assert rep.pay_rate == 13200 and any("differs" in w for w in rep.warnings)


def test_payments_and_totals(tmp_path):
    p = tmp_path / "payments.csv"
    assert len(store.load_payments(p)) == 0                     # starts empty
    first = store.add_payment(dt.date(2026, 1, 5), 10000, 13385, path=p)
    row = store.add_payment(dt.date(2026, 2, 3), 10000, 13300, path=p)
    assert first["n"] == 1 and first["grams"] == pytest.approx(0.7471, abs=1e-4)
    assert row["n"] == 2 and row["grams"] == pytest.approx(0.7519, abs=1e-4)
    t = store.totals(store.load_payments(p))
    assert t["instalments"] == 2 and t["grams"] == pytest.approx(1.4990, abs=1e-4)


def test_save_rates_upserts(tmp_path):
    p = tmp_path / "rates.csv"
    d = dt.date(2026, 11, 6)
    assert store.save_rates([RateRecord("mjdta", d, 13140, "10:32")], p) == 1
    assert store.save_rates([RateRecord("mjdta", d, 13140, "10:32")], p) == 0


def _series(daily_change: float, days: int = 400, seed: int = 1) -> pd.Series:
    rng = np.random.default_rng(seed)
    r = daily_change + rng.normal(0, 0.004, days)
    idx = pd.bdate_range(end="2026-11-02", periods=days + 1)
    return pd.Series(13000 * np.exp(np.concatenate([[0], np.cumsum(r)])), index=idx)


def test_rising_trend_says_pay_now():
    s = _series(+0.004)
    res = analysis.decide(s, s.index[-1].date(), margin=0.002, n_left=6, paths=2000)
    assert res["action"] == "PAY"


def test_falling_trend_says_wait():
    s = _series(-0.004)
    res = analysis.decide(s, s.index[-1].date(), margin=0.002, n_left=6, paths=2000)
    assert res["action"] == "WAIT" and res["expected_wait_rate"] < res["rate"]


def test_deadline_day_always_pays():
    s = _series(-0.004)
    res = analysis.decide(s, s.index[-1].date(), margin=0.002, n_left=0)
    assert res["action"] == "PAY"


def test_remaining_window_days_skips_sundays():
    assert analysis.remaining_window_days(dt.date(2026, 12, 10), 10) == 0
    # 1 Dec 2026 is a Tuesday: 2-10 Dec has one Sunday (6th) -> 8 days
    assert analysis.remaining_window_days(dt.date(2026, 12, 1), 10) == 8
